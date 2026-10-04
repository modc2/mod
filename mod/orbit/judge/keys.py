"""keys — judge keyrings: multiple signature key types, quantum-resistant included.

Every judge gets a keyring the moment it first sits on a panel: one keypair
per available key type. Every vote the judge casts is then signed by every
key in the ring (hybrid signing), so a forged or tampered verdict record
has to defeat every scheme at once — the record stays trustworthy even if
one scheme falls, e.g. to a quantum computer breaking Ed25519.

Key types:
  ed25519      EdDSA over Curve25519 (cryptography lib). Classical — fast,
               tiny signatures, NOT quantum-resistant; kept for today's
               tooling and as the hybrid's classical leg.
  ml-dsa-65    FIPS 204 ML-DSA (module-lattice, dilithium-py). Quantum-
               resistant, NIST category 3.
  wots-sha256  Stateful Winternitz one-time chains under a Merkle tree,
               SHA-256 only — quantum-resistant assuming nothing but the
               hash, and pure stdlib, so a judge always has at least one
               post-quantum key even on a box with no crypto libs. Each
               key signs at most 2**JUDGE_XMSS_HEIGHT votes (default 2**8);
               when a key is spent the ring's other types keep signing.

Secrets never leave the local store; only public keys are published.
"""

import hashlib
import json
import os

try:
    from cryptography.hazmat.primitives.asymmetric import ed25519 as _ed
    from cryptography.exceptions import InvalidSignature as _BadSig
except Exception:  # pragma: no cover — lib present on this box
    _ed = None

try:
    from dilithium_py.ml_dsa import ML_DSA_65 as _mldsa
except Exception:  # pragma: no cover
    _mldsa = None


def _height():
    return int(os.environ.get('JUDGE_XMSS_HEIGHT', 8))


def kinds():
    """Every key type this build knows, and whether it can be used here."""
    return {
        'ed25519': {'available': _ed is not None, 'quantum_resistant': False,
                    'algo': 'EdDSA / Curve25519'},
        'ml-dsa-65': {'available': _mldsa is not None, 'quantum_resistant': True,
                      'algo': 'FIPS 204 ML-DSA-65 (lattice)'},
        'wots-sha256': {'available': True, 'quantum_resistant': True,
                        'algo': f'WOTS+Merkle / SHA-256, 2^{_height()} sigs'},
    }


def available():
    return [k for k, v in kinds().items() if v['available']]


def fingerprint(public_hex):
    return hashlib.sha256(bytes.fromhex(public_hex)).hexdigest()[:16]


def generate(ktype):
    """New keypair → {'public': hex, 'secret': dict, 'state': dict}."""
    if ktype == 'ed25519':
        sk = _ed.Ed25519PrivateKey.generate()
        return {'public': sk.public_key().public_bytes_raw().hex(),
                'secret': {'sk': sk.private_bytes_raw().hex()}, 'state': {}}
    if ktype == 'ml-dsa-65':
        pk, sk = _mldsa.keygen()
        return {'public': pk.hex(), 'secret': {'sk': sk.hex()}, 'state': {}}
    if ktype == 'wots-sha256':
        return _wots_generate(_height())
    raise ValueError(f'unknown key type {ktype!r}')


def sign(ktype, secret, state, msg):
    """Sign msg bytes → (sig_hex | None, note | None). May mutate state
    (the hash-based type consumes a one-time leaf per signature)."""
    if ktype == 'ed25519':
        sk = _ed.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret['sk']))
        return sk.sign(msg).hex(), None
    if ktype == 'ml-dsa-65':
        return _mldsa.sign(bytes.fromhex(secret['sk']), msg).hex(), None
    if ktype == 'wots-sha256':
        return _wots_sign(secret, state, msg)
    return None, f'unknown key type {ktype!r}'


def verify(ktype, public_hex, sig_hex, msg):
    """True iff sig_hex is a valid signature on msg under public_hex."""
    try:
        if ktype == 'ed25519':
            pk = _ed.Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
            pk.verify(bytes.fromhex(sig_hex), msg)
            return True
        if ktype == 'ml-dsa-65':
            return bool(_mldsa.verify(bytes.fromhex(public_hex), msg,
                                      bytes.fromhex(sig_hex)))
        if ktype == 'wots-sha256':
            return _wots_verify(public_hex, bytes.fromhex(sig_hex), msg)
    except Exception:
        return False
    return False


# ── wots-sha256: stdlib hash-based signatures ────────────────────
#
# Winternitz chains (w=16) over SHA-256, one chain set per Merkle leaf.
# Chain steps are domain-tagged with (chain index, position) so no two
# chains share a hash function. All chain secrets derive from one seed,
# so the stored secret is just the seed plus the leaf hashes (needed to
# rebuild the auth path cheaply). Signing consumes leaf state['next'].

_W = 16
_L1 = 64                 # 32-byte digest → 64 base-16 digits
_L2 = 3                  # checksum ≤ 64·15 = 960 → 3 digits
_L = _L1 + _L2


def _f(chain, pos, x):
    return hashlib.sha256(b'judge-wots' + chain.to_bytes(2, 'big')
                          + pos.to_bytes(2, 'big') + x).digest()


def _chain(x, chain, start, steps):
    for p in range(start, start + steps):
        x = _f(chain, p, x)
    return x


def _digits(digest):
    d = []
    for b in digest:
        d += [b >> 4, b & 15]
    c = sum(_W - 1 - x for x in d)
    return d + [(c >> 8) & 15, (c >> 4) & 15, c & 15]


def _leaf_sks(seed, leaf):
    return [hashlib.sha256(seed + b'sk' + leaf.to_bytes(4, 'big')
                           + i.to_bytes(2, 'big')).digest() for i in range(_L)]


def _leaf_hash(sks):
    pks = [_chain(sk, i, 0, _W - 1) for i, sk in enumerate(sks)]
    return hashlib.sha256(b'leaf' + b''.join(pks)).digest()


def _levels(leaves):
    levels = [leaves]
    while len(levels[-1]) > 1:
        cur = levels[-1]
        levels.append([hashlib.sha256(b'node' + cur[i] + cur[i + 1]).digest()
                       for i in range(0, len(cur), 2)])
    return levels


def _wots_generate(height):
    seed = os.urandom(32)
    leaves = [_leaf_hash(_leaf_sks(seed, j)) for j in range(2 ** height)]
    root = _levels(leaves)[-1][0]
    return {'public': root.hex(),
            'secret': {'seed': seed.hex(), 'height': height,
                       'leaves': [l.hex() for l in leaves]},
            'state': {'next': 0}}


def _wots_sign(secret, state, msg):
    j = int(state.get('next', 0))
    cap = 2 ** int(secret['height'])
    if j >= cap:
        return None, f'key exhausted ({cap} one-time leaves spent)'
    seed = bytes.fromhex(secret['seed'])
    d = _digits(hashlib.sha256(msg).digest())
    sks = _leaf_sks(seed, j)
    parts = [_chain(sks[i], i, 0, d[i]) for i in range(_L)]
    leaves = [bytes.fromhex(l) for l in secret['leaves']]
    auth, idx = [], j
    for level in _levels(leaves)[:-1]:
        auth.append(level[idx ^ 1])
        idx >>= 1
    state['next'] = j + 1
    return (j.to_bytes(4, 'big') + b''.join(parts) + b''.join(auth)).hex(), None


def _wots_verify(public_hex, sig, msg):
    if (len(sig) - 4 - _L * 32) % 32 or len(sig) < 4 + _L * 32:
        return False
    j = int.from_bytes(sig[:4], 'big')
    parts = [sig[4 + i * 32: 4 + (i + 1) * 32] for i in range(_L)]
    auth = sig[4 + _L * 32:]
    auth = [auth[i * 32:(i + 1) * 32] for i in range(len(auth) // 32)]
    d = _digits(hashlib.sha256(msg).digest())
    pks = [_chain(parts[i], i, d[i], _W - 1 - d[i]) for i in range(_L)]
    node, idx = hashlib.sha256(b'leaf' + b''.join(pks)).digest(), j
    for a in auth:
        node = hashlib.sha256(b'node' + (a + node if idx & 1 else node + a)).digest()
        idx >>= 1
    return node.hex() == public_hex
