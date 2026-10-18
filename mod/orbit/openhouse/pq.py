"""
pq.py — post-quantum signatures for the bank's attestations.

A rent receipt is only as strong as the signature on its fact. ed25519 and
secp256k1 fall to Shor's algorithm: whoever runs a large enough quantum
computer can forge a bank's signature and "prove" rent nobody paid. Hashes do
not fall that way — Grover only halves their bits, and a 256-bit keccak still
leaves 128. So the post-quantum layer is built out of the one primitive every
verifier already has natively:

    OHX-KECCAK   a stateful hash-based signature (XMSS^MT shape: WOTS+ one-
                 time keys under a two-layer Merkle hypertree), keccak256
                 only. Ethereum (keccak opcode), NEAR (env::keccak256), Cairo
                 and Python all verify it — so it is the ON-CHAIN post-quantum
                 door. 2^16 = 65,536 signatures per key; 4,804-byte sigs.
    ML-DSA-65    FIPS 204 (Dilithium), stateless, NIST category 3. Checked
                 off-chain (too heavy for a chain today). Loaded from
                 orbit/postquant's pure-python implementation when present.

A receipt carries both next to the classical pair. Any one valid PQ
signature (and no invalid one) makes it quantum-safe.

OHX-KECCAK, byte for byte (contracts/OHX.sol and near/src/lib.rs are twins):

    n = 32, w = 16, L = 64 message digits + 3 checksum digits = 67 chains
    H = 8 per layer, D = 2 layers  →  2^16 leaves
    ADRS(layer, tree, type, a, b, c) = 32-byte big-endian word
        layer<<224 | tree<<160 | type<<128 | a<<96 | b<<64 | c<<32
    types: CHAIN=0 (a=keypair b=chain c=step)  LEAF=1 (a=keypair)
           NODE=2 (b=height c=index)  PRF=3 (a=keypair b=chain)  MSG=4 (a=keypair)
    F(adrs, x)       = keccak(pub_seed ‖ adrs ‖ x)
    digest           = keccak(pub_seed ‖ ADRS(l,t,MSG,kp) ‖ message)
    digits           = 64 nibbles of digest, high nibble first, then
                       csum = Σ(15 − d) as 3 nibbles (csum>>8, csum>>4, csum) & 15
    leaf             = keccak(pub_seed ‖ ADRS(l,t,LEAF,kp) ‖ pk_0 ‖ … ‖ pk_66)
    node             = keccak(pub_seed ‖ ADRS(l,t,NODE,0,h,i) ‖ left ‖ right)
                       (h = the parent's height 1..H, i = the parent's index)
    public key       = (pub_seed, root of the layer-1 tree)
    signature        = idx (u32 BE) ‖ [ wots 67×32 ‖ auth path H×32 ] × D
    layer 0 signs the message at keypair idx & 255 of tree idx >> 8;
    layer 1 signs that tree's root at keypair idx >> 8 of tree 0.

Stateful means: a leaf signs ONCE. The signer reserves the index on disk
before it releases a signature, and chains burn the index they accept.

No openhouse imports — liftable into any module.
"""
import hashlib
import importlib.util
import json
import os
import secrets as _secrets
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

N = 32
W = 16
L1 = 64
L2 = 3
L = L1 + L2
H = 8
D = 2
CAPACITY = 1 << (H * D)
SIG_BYTES = 4 + D * (L * N + H * N)

T_CHAIN, T_LEAF, T_NODE, T_PRF, T_MSG = 0, 1, 2, 3, 4

SCHEME = 'OHX-KECCAK'
SCHEME_ID = 'ohx-keccak-w16-h8x2'
MLDSA = 'ML-DSA-65'

_LOCK = threading.Lock()


class PQError(Exception):
    """A refusal with a reason — surfaced as {'error': ...}, never a 500."""


def keccak(data: bytes) -> bytes:
    try:
        from eth_hash.auto import keccak as _k
        return bytes(_k(data))
    except Exception:
        from Crypto.Hash import keccak as _kk
        return _kk.new(digest_bits=256, data=data).digest()


def adrs(layer: int, tree: int, typ: int, a: int = 0, b: int = 0, c: int = 0) -> bytes:
    v = (layer << 224) | (tree << 160) | (typ << 128) | (a << 96) | (b << 64) | (c << 32)
    return v.to_bytes(32, 'big')


def digits(msg: bytes, pub_seed: bytes, layer: int, tree: int, kp: int) -> list:
    d = keccak(pub_seed + adrs(layer, tree, T_MSG, kp) + msg)
    out = []
    for byte in d:
        out += [byte >> 4, byte & 15]
    cs = sum(W - 1 - x for x in out)
    return out + [(cs >> 8) & 15, (cs >> 4) & 15, cs & 15]


def _chain(pub_seed: bytes, x: bytes, start: int, steps: int, layer: int, tree: int, kp: int, ch: int) -> bytes:
    for j in range(start, start + steps):
        x = keccak(pub_seed + adrs(layer, tree, T_CHAIN, kp, ch, j) + x)
    return x


def _leaf(pub_seed: bytes, pks: list, layer: int, tree: int, kp: int) -> bytes:
    return keccak(pub_seed + adrs(layer, tree, T_LEAF, kp) + b''.join(pks))


def _node(pub_seed: bytes, left: bytes, right: bytes, layer: int, tree: int, h: int, i: int) -> bytes:
    return keccak(pub_seed + adrs(layer, tree, T_NODE, 0, h, i) + left + right)


# ── verify (public, no state) ───────────────────────────────────

def verify(pub_seed: bytes, root: bytes, msg: bytes, sig: bytes) -> bool:
    """True iff `sig` is an OHX-KECCAK signature on the 32-byte `msg` under
    (pub_seed, root). Never raises on malformed input."""
    try:
        if len(pub_seed) != N or len(root) != N or len(msg) != N or len(sig) != SIG_BYTES:
            return False
        idx = int.from_bytes(sig[:4], 'big')
        if idx >= CAPACITY:
            return False
        node, t, off = msg, idx, 4
        for layer in range(D):
            kp, tree = t & ((1 << H) - 1), t >> H
            ds = digits(node, pub_seed, layer, tree, kp)
            pks = []
            for ch in range(L):
                s = sig[off + ch * N: off + (ch + 1) * N]
                pks.append(_chain(pub_seed, s, ds[ch], W - 1 - ds[ch], layer, tree, kp, ch))
            off += L * N
            cur, i = _leaf(pub_seed, pks, layer, tree, kp), kp
            for h in range(H):
                sib = sig[off + h * N: off + (h + 1) * N]
                left, right = (cur, sib) if i % 2 == 0 else (sib, cur)
                i >>= 1
                cur = _node(pub_seed, left, right, layer, tree, h + 1, i)
            off += H * N
            node, t = cur, tree
        return node == root
    except Exception:
        return False


def sig_index(sig: bytes) -> int:
    return int.from_bytes(sig[:4], 'big') if len(sig) >= 4 else -1


def key_id(pub_seed: bytes, root: bytes) -> str:
    """The bank's post-quantum identity: keccak(pub_seed ‖ root)."""
    return '0x' + keccak(pub_seed + root).hex()


# ── sign (secret, stateful) ─────────────────────────────────────

@lru_cache(maxsize=8)
def _tree(sk_seed: bytes, pub_seed: bytes, layer: int, tree: int):
    """Every level of one H-tall tree, leaves first. ~0.5s cold in Python,
    cached after; a signer touches a new bottom tree every 256 signatures."""
    leaves = []
    for kp in range(1 << H):
        pks = [_chain(pub_seed, keccak(sk_seed + adrs(layer, tree, T_PRF, kp, ch)), 0, W - 1, layer, tree, kp, ch)
               for ch in range(L)]
        leaves.append(_leaf(pub_seed, pks, layer, tree, kp))
    levels = [leaves]
    for h in range(1, H + 1):
        prev = levels[-1]
        levels.append([_node(pub_seed, prev[2 * i], prev[2 * i + 1], layer, tree, h, i)
                       for i in range(len(prev) // 2)])
    return levels


def public_root(sk_seed: bytes, pub_seed: bytes, trees=None) -> bytes:
    return (trees or (lambda l, t: _tree(sk_seed, pub_seed, l, t)))(D - 1, 0)[H][0]


def sign_at(sk_seed: bytes, pub_seed: bytes, msg: bytes, idx: int, trees=None) -> bytes:
    """Sign at an explicit leaf. The caller owns leaf bookkeeping — use
    Signer.sign, which reserves the leaf on disk first. `trees(layer, tree)`
    may serve cached tree levels (Signer keeps them on disk)."""
    trees = trees or (lambda l, t: _tree(sk_seed, pub_seed, l, t))
    if len(msg) != N:
        raise PQError('OHX-KECCAK signs 32-byte messages')
    if not 0 <= idx < CAPACITY:
        raise PQError('leaf index out of range')
    out = [idx.to_bytes(4, 'big')]
    node, t = msg, idx
    for layer in range(D):
        kp, tree = t & ((1 << H) - 1), t >> H
        ds = digits(node, pub_seed, layer, tree, kp)
        for ch in range(L):
            out.append(_chain(pub_seed, keccak(sk_seed + adrs(layer, tree, T_PRF, kp, ch)), 0, ds[ch],
                              layer, tree, kp, ch))
        levels = trees(layer, tree)
        i = kp
        for h in range(H):
            out.append(levels[h][i ^ 1])
            i >>= 1
        node, t = levels[H][0], tree
    return b''.join(out)


# ── ML-DSA-65 (FIPS 204), borrowed from orbit/postquant ─────────

def _mldsa():
    """orbit/postquant's pure-python ML-DSA, loaded by path. None if the
    module is not on this box — the receipt then carries OHX alone."""
    name = 'openhouse_pq_mldsa'
    if name in sys.modules:
        return sys.modules[name]
    here = Path(__file__).resolve().parent
    for cand in (here.parent / 'postquant' / 'src' / 'pq' / 'mldsa.py',
                 Path(os.environ.get('OPENHOUSE_MLDSA', '/nonexistent'))):
        if cand.exists():
            try:
                spec = importlib.util.spec_from_file_location(name, cand)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                sys.modules[name] = mod
                return mod
            except Exception:
                return None
    return None


def mldsa_available() -> bool:
    return _mldsa() is not None


MLDSA_CTX = b'openhouse/proof-fact/v1'


def mldsa_verify(pk: bytes, msg: bytes, sig: bytes) -> Optional[bool]:
    m = _mldsa()
    if m is None:
        return None
    try:
        return bool(m.verify(pk, msg, sig, MLDSA, ctx=MLDSA_CTX))
    except Exception:
        return False


# ── the signer (a bank's post-quantum keys) ─────────────────────

def _write_private(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class Signer:
    """One bank's post-quantum identity, in secrets_dir/proof_pq.json (0600):
    an OHX-KECCAK seed pair + the next unused leaf, and an ML-DSA-65 seed.
    `retired` keeps every exhausted or rotated OHX public key so old receipts
    stay checkable. Nothing here returns a secret."""

    def __init__(self, secrets_dir):
        self.path = Path(secrets_dir) / 'proof_pq.json'
        self._d = None

    def _load(self) -> dict:
        if self._d is None:
            if self.path.exists():
                with open(self.path) as f:
                    self._d = json.load(f)
            else:
                self._d = self._fresh()
                _write_private(self.path, self._d)
        return self._d

    @staticmethod
    def _fresh(retired=None) -> dict:
        return {'scheme': SCHEME_ID, 'sk_seed': _secrets.token_hex(32), 'pub_seed': _secrets.token_hex(32),
                'next': 0, 'mldsa_seed': _secrets.token_hex(32), 'created': int(time.time()),
                'retired': retired or []}

    def _seeds(self):
        d = self._load()
        return bytes.fromhex(d['sk_seed']), bytes.fromhex(d['pub_seed'])

    def _trees(self, layer: int, tree: int):
        """Tree levels, built once (~3s in Python) and kept beside the key.
        Nodes are public — anything here could be published — but the files
        sit in the secrets dir so they share the key's lifetime."""
        sk, ps = self._seeds()
        cache = self.path.parent / 'proof_pq_trees' / f'{ps.hex()[:16]}-{layer}-{tree}.json'
        if cache.exists():
            try:
                with open(cache) as f:
                    return [[bytes.fromhex(x) for x in lvl] for lvl in json.load(f)]
            except Exception:
                pass
        levels = _tree(sk, ps, layer, tree)
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix('.tmp')
        with open(tmp, 'w') as f:
            json.dump([[x.hex() for x in lvl] for lvl in levels], f)
        os.replace(tmp, cache)
        return levels

    def ohx_public(self) -> dict:
        d = self._load()
        sk, ps = self._seeds()
        if not d.get('root'):
            d['root'] = public_root(sk, ps, self._trees).hex()
            _write_private(self.path, d)
        root = bytes.fromhex(d['root'])
        return {'scheme': SCHEME, 'params': SCHEME_ID, 'id': key_id(ps, root),
                'pub_seed': '0x' + ps.hex(), 'root': '0x' + root.hex()}

    def _mldsa_keys(self):
        m = _mldsa()
        if m is None:
            return None, None
        seed = bytes.fromhex(self._load()['mldsa_seed'])
        return m.keygen(MLDSA, seed=seed)

    def mldsa_public(self) -> Optional[dict]:
        pk, _ = self._mldsa_keys()
        if pk is None:
            return None
        return {'scheme': MLDSA, 'pk': '0x' + pk.hex(), 'fingerprint': '0x' + hashlib.sha3_256(pk).hexdigest(),
                'ctx': MLDSA_CTX.decode()}

    def public(self) -> dict:
        out = {'ohx': self.ohx_public()}
        ml = self.mldsa_public()
        if ml:
            out['mldsa65'] = ml
        return out

    def capacity(self) -> dict:
        d = self._load()
        used = int(d.get('next', 0))
        return {'capacity': CAPACITY, 'used': used, 'left': CAPACITY - used,
                'retired_keys': len(d.get('retired') or []),
                'warning': ('rotate soon — under 10% of one-time leaves left' if CAPACITY - used < CAPACITY // 10 else '')}

    def _reserve(self) -> int:
        with _LOCK:
            d = self._load()
            idx = int(d.get('next', 0))
            if idx >= CAPACITY:
                raise PQError('OHX-KECCAK key exhausted — rotate it (proof_pq_rotate) before signing again')
            d['next'] = idx + 1
            _write_private(self.path, d)   # on disk BEFORE the signature exists
            return idx

    def sign(self, fact_hex: str) -> dict:
        msg = bytes.fromhex(fact_hex[2:] if fact_hex.startswith('0x') else fact_hex)
        sk, ps = self._seeds()
        self.ohx_public()
        idx = self._reserve()
        out = {'ohx': '0x' + sign_at(sk, ps, msg, idx, self._trees).hex()}
        pk, skm = self._mldsa_keys()
        if skm is not None:
            out['mldsa65'] = '0x' + _mldsa().sign(skm, msg, MLDSA, ctx=MLDSA_CTX).hex()
        return out

    def rotate(self) -> dict:
        """Retire the OHX key (its public half stays listed) and draw a new
        one. ML-DSA is stateless and keeps its seed."""
        with _LOCK:
            old = self.ohx_public()
            d = self._load()
            retired = list(d.get('retired') or []) + [{**old, 'used': int(d.get('next', 0)), 'retired': int(time.time())}]
            fresh = self._fresh(retired)
            fresh['mldsa_seed'] = d['mldsa_seed']
            self._d = fresh
            _write_private(self.path, fresh)
        return {'retired': old, 'current': self.ohx_public()}

    def retired(self) -> list:
        return list(self._load().get('retired') or [])


# ── checking a receipt's post-quantum signatures ────────────────

def check(fact_hex: str, bank_pq: dict, sig_pq: dict) -> dict:
    """{safe, checks[]} for the PQ half of a receipt. safe = at least one PQ
    signature verifies and none fails."""
    checks = []
    bank_pq, sig_pq = bank_pq or {}, sig_pq or {}
    try:
        msg = bytes.fromhex(fact_hex[2:])
    except Exception:
        return {'safe': False, 'checks': [{'claim': 'fact is hex', 'ok': False}]}
    o = bank_pq.get('ohx') or {}
    if o or sig_pq.get('ohx'):
        try:
            ps, rt = bytes.fromhex(o['pub_seed'][2:]), bytes.fromhex(o['root'][2:])
            s = bytes.fromhex(str(sig_pq.get('ohx', ''))[2:])
            ok = verify(ps, rt, msg, s) and key_id(ps, rt) == str(o.get('id', '')).lower()
            leaf = sig_index(s)
        except Exception:
            ok, leaf = False, -1
        checks.append({'claim': f'{SCHEME} hash-based signature (leaf {leaf}) by {str(o.get("id", "?"))[:14]}…',
                       'ok': ok, 'pq': True})
    m = bank_pq.get('mldsa65') or {}
    if m or sig_pq.get('mldsa65'):
        try:
            pk = bytes.fromhex(m['pk'][2:])
            ok = mldsa_verify(pk, msg, bytes.fromhex(str(sig_pq.get('mldsa65', ''))[2:]))
            if ok and m.get('fingerprint'):
                ok = '0x' + hashlib.sha3_256(pk).hexdigest() == m['fingerprint'].lower()
        except Exception:
            ok = False
        checks.append({'claim': f'{MLDSA} (FIPS 204) lattice signature', 'ok': ok, 'pq': True})
    verdicts = [c['ok'] for c in checks]
    safe = any(v is True for v in verdicts) and not any(v is False for v in verdicts)
    return {'safe': safe, 'checks': checks}


def threat_model() -> list:
    """What a quantum computer breaks here, and what it does not."""
    return [
        {'part': 'commitment + nullifier (Poseidon)', 'quantum': 'safe',
         'why': 'hash-based; Grover only halves preimage bits, 251-bit field leaves ~125'},
        {'part': 'the fact (keccak256)', 'quantum': 'safe', 'why': 'hash; 128-bit post-quantum preimage security'},
        {'part': 'STARK door (fact registry)', 'quantum': 'safe',
         'why': 'STARKs are hash-based with a transparent setup — no curve, no pairing'},
        {'part': f'bank signature, {SCHEME}', 'quantum': 'safe',
         'why': 'WOTS+/Merkle over keccak only; verified on Ethereum and NEAR today'},
        {'part': f'bank signature, {MLDSA}', 'quantum': 'safe', 'why': 'NIST FIPS 204 lattice signature; checked off-chain'},
        {'part': 'bank signature, ed25519 / secp256k1', 'quantum': 'breakable',
         'why': "Shor recovers the key from the public key; kept for today's wallets, closable per chain"},
        {'part': 'chain accounts (relayer, contract owner)', 'quantum': 'breakable',
         'why': 'Ethereum EOAs and NEAR ed25519 keys are the chains\' own — rotate to PQ keys when the chains offer them'},
    ]
