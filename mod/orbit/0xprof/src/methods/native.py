"""
The native method — this box's own arithmetic, in Python, depending on nobody.

Everything in here re-derives the answer from the proof and the verification
key alone. No node, no RPC, no toolchain: py_ecc for the pairing curves and a
few dozen lines of modular arithmetic for the rest. That independence is the
entire point. `snarkjs` is the reference implementation and is very probably
right; the value of asking a second implementation the same question is that
the two were not written by the same hand, so a bug has to be in both to go
unnoticed.

What it can check:

    groth16     bn128 and bls12-381, snarkjs verification-key JSON
    merkle      sha256 / keccak256 inclusion, either sibling order
    schnorr     Fiat-Shamir proof of knowledge of a secp256k1 discrete log
    dleq        Chaum-Pedersen: two points share an exponent, without it
    pedersen    an opening of a Pedersen commitment (hiding, not zero-knowledge)

plonk and fflonk are deliberately absent: a partial verifier that returns True
by not checking the hard part is worse than no verifier, so those systems say
`unavailable` here and are answered by the methods that really do verify them.
"""
import hashlib
from typing import Any, Dict, List, Optional

CURVES = {'bn128': 'bn128', 'bn254': 'bn128', 'altbn128': 'bn128',
          'bls12381': 'bls12381', 'bls12-381': 'bls12381'}


class VerifyError(Exception):
    """The proof is malformed — a different thing from the proof being false."""


# ── field / point plumbing ───────────────────────────────────────────

def _int(value: Any) -> int:
    """snarkjs writes field elements as decimal strings; chains write hex."""
    if isinstance(value, bool):
        raise VerifyError('a boolean is not a field element')
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith('0x'):
            return int(text, 16)
        if text:
            return int(text)
    raise VerifyError(f'not a field element: {value!r}')


def _curve(name: str):
    key = CURVES.get((name or 'bn128').lower().replace('_', '-'))
    if key == 'bn128':
        from py_ecc import optimized_bn128 as curve
        return curve
    if key == 'bls12381':
        from py_ecc import optimized_bls12_381 as curve
        return curve
    raise VerifyError(f'unknown curve {name!r} — bn128 or bls12-381')


def _g1(curve, point: List[Any]):
    """[x, y, z] in the base field, as snarkjs writes G1."""
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        raise VerifyError(f'G1 point wants [x, y, z], got {point!r}')
    z = _int(point[2]) if len(point) > 2 else 1
    got = (curve.FQ(_int(point[0])), curve.FQ(_int(point[1])), curve.FQ(z))
    if z == 0:
        return curve.Z1                       # the point at infinity
    if not curve.is_on_curve(got, curve.b):
        raise VerifyError('G1 point is not on the curve')
    return got


def _g2(curve, point: List[List[Any]]):
    """[[x0,x1],[y0,y1],[z0,z1]] — each coordinate an Fp2 element, c0 first."""
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        raise VerifyError(f'G2 point wants [x, y, z], got {point!r}')

    def fq2(pair):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise VerifyError(f'Fp2 coordinate wants two elements, got {pair!r}')
        return curve.FQ2([_int(pair[0]), _int(pair[1])])

    z = fq2(point[2]) if len(point) > 2 else curve.FQ2.one()
    if z == curve.FQ2.zero():
        return curve.Z2
    got = (fq2(point[0]), fq2(point[1]), z)
    if not curve.is_on_curve(got, curve.b2):
        raise VerifyError('G2 point is not on the twist')
    return got


# ── groth16 ──────────────────────────────────────────────────────────

def groth16(proof: Dict[str, Any], vkey: Dict[str, Any],
            public_signals: List[Any]) -> Dict[str, Any]:
    """The pairing check, written out.

        e(A, B) == e(alpha, beta) * e(vk_x, gamma) * e(C, delta)

    where vk_x is IC[0] + sum(public[i] * IC[i+1]) — the public inputs folded
    into a single group element. Rearranged into one product against the
    identity so the final exponentiation is paid for once instead of four
    times, which is most of the cost.
    """
    curve = _curve(vkey.get('curve') or proof.get('curve') or 'bn128')
    protocol = (vkey.get('protocol') or proof.get('protocol') or 'groth16').lower()
    if protocol != 'groth16':
        raise VerifyError(f'this is a {protocol} key, not groth16')

    ic = vkey.get('IC') or []
    signals = [_int(s) for s in (public_signals or [])]
    if len(ic) != len(signals) + 1:
        raise VerifyError(
            f'key expects {len(ic) - 1} public signals, got {len(signals)} — '
            'the proof and the key are for different circuits')
    for signal in signals:
        if not 0 <= signal < curve.curve_order:
            raise VerifyError('public signal is outside the scalar field')

    # vk_x — the only multi-scalar multiplication a groth16 verifier does.
    vk_x = _g1(curve, ic[0])
    for signal, point in zip(signals, ic[1:]):
        vk_x = curve.add(vk_x, curve.multiply(_g1(curve, point), signal))

    a, b, c = (_g1(curve, proof['pi_a']), _g2(curve, proof['pi_b']),
               _g1(curve, proof['pi_c']))
    alpha, beta = _g1(curve, vkey['vk_alpha_1']), _g2(curve, vkey['vk_beta_2'])
    gamma, delta = _g2(curve, vkey['vk_gamma_2']), _g2(curve, vkey['vk_delta_2'])

    product = (curve.pairing(b, curve.neg(a), final_exponentiate=False)
               * curve.pairing(beta, alpha, final_exponentiate=False)
               * curve.pairing(gamma, vk_x, final_exponentiate=False)
               * curve.pairing(delta, c, final_exponentiate=False))
    ok = curve.final_exponentiate(product) == curve.FQ12.one()
    return {
        'ok': bool(ok),
        'detail': {
            'curve': vkey.get('curve') or 'bn128',
            'public_signals': len(signals),
            'check': 'e(-A,B)*e(alpha,beta)*e(vk_x,gamma)*e(C,delta) == 1',
            'pairings': 4,
        },
    }


# ── merkle inclusion ─────────────────────────────────────────────────

HASHES = {
    'sha256': lambda data: hashlib.sha256(data).digest(),
    'sha512-256': lambda data: hashlib.new('sha512_256', data).digest(),
    'blake2s': lambda data: hashlib.blake2s(data).digest(),
}


def _keccak(data: bytes) -> bytes:
    from Crypto.Hash import keccak as _k
    return _k.new(digest_bits=256).update(data).digest()


def _hash(name: str):
    name = (name or 'sha256').lower().replace('_', '-')
    if name in ('keccak256', 'keccak-256', 'keccak'):
        try:
            _keccak(b'')
        except Exception as e:                 # pycryptodome absent
            raise VerifyError(f'keccak256 needs pycryptodome: {e}')
        return _keccak
    if name not in HASHES:
        raise VerifyError(f'unknown hash {name} — one of {sorted(HASHES) + ["keccak256"]}')
    return HASHES[name]


def _bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    text = str(value)
    if text.lower().startswith('0x'):
        text = text[2:]
        if len(text) % 2:
            text = '0' + text
        return bytes.fromhex(text)
    return text.encode()


def merkle(proof: Dict[str, Any], vkey: Dict[str, Any],
           public_signals: List[Any]) -> Dict[str, Any]:
    """Walk the path from a leaf to a root and see whether you land on it.

    Not a zk proof — it hides nothing but the rest of the tree — and it is here
    because half the "proofs" traded in practice are inclusion proofs, and a
    market that can only price pairings would refuse most of its own business.
    The `system` field says exactly what it is, so nobody is misled about which
    of these is zero-knowledge.
    """
    statement = vkey or {}
    root = _bytes(statement.get('root') or proof.get('root') or '')
    leaf_raw = proof.get('leaf')
    if leaf_raw is None and public_signals:
        leaf_raw = public_signals[0]
    if not root or leaf_raw is None:
        raise VerifyError('a merkle proof needs a root and a leaf')

    digest = _hash(statement.get('hash') or proof.get('hash') or 'sha256')
    prehash = bool(statement.get('hash_leaf', proof.get('hash_leaf', True)))
    node = digest(_bytes(leaf_raw)) if prehash else _bytes(leaf_raw)
    sorted_pairs = bool(statement.get('sorted', proof.get('sorted', False)))

    path = proof.get('path') or proof.get('siblings') or []
    if not isinstance(path, list):
        raise VerifyError('path must be a list of {sibling, position} steps')
    for depth, step in enumerate(path):
        if isinstance(step, (str, bytes)):
            step = {'sibling': step, 'position': 'right'}
        sibling = _bytes(step.get('sibling') or step.get('hash') or '')
        if not sibling:
            raise VerifyError(f'step {depth} has no sibling')
        position = str(step.get('position', step.get('side', 'right'))).lower()
        if sorted_pairs:                       # OpenZeppelin-style ordered pairs
            left, right = sorted([node, sibling])
        elif position in ('left', '0', 'false'):
            left, right = sibling, node
        else:
            left, right = node, sibling
        node = digest(left + right)

    return {
        'ok': node == root,
        'detail': {'depth': len(path), 'hash': statement.get('hash', 'sha256'),
                   'computed_root': '0x' + node.hex(), 'expected_root': '0x' + root.hex(),
                   'sorted_pairs': sorted_pairs},
    }


# ── secp256k1, by hand ───────────────────────────────────────────────
#
# Sigma protocols are small enough that pulling in a curve library costs more
# than writing the curve. Fifty lines, no dependency, and the module keeps
# working on a box where pip cannot reach the network.

P = 2**256 - 2**32 - 977
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
Gx = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
Gy = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
G = (Gx, Gy)


def _pt_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        lam = 3 * x1 * x1 % P * pow(2 * y1 % P, P - 2, P) % P
    else:
        lam = (y2 - y1) * pow((x2 - x1) % P, P - 2, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return (x3, (lam * (x1 - x3) - y1) % P)


def _pt_mul(point, scalar: int):
    scalar %= N
    result, addend = None, point
    while scalar:
        if scalar & 1:
            result = _pt_add(result, addend)
        addend = _pt_add(addend, addend)
        scalar >>= 1
    return result


def _on_curve(point) -> bool:
    if point is None:
        return False
    x, y = point
    return (y * y - x * x * x - 7) % P == 0


def _decode_point(value: Any):
    """Uncompressed (04||x||y), compressed (02/03||x), or {x, y}."""
    if isinstance(value, dict):
        point = (_int(value['x']), _int(value['y']))
        if not _on_curve(point):
            raise VerifyError('point is not on secp256k1')
        return point
    raw = _bytes(value)
    if len(raw) == 65 and raw[0] == 4:
        point = (int.from_bytes(raw[1:33], 'big'), int.from_bytes(raw[33:], 'big'))
    elif len(raw) == 64:
        point = (int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big'))
    elif len(raw) == 33 and raw[0] in (2, 3):
        x = int.from_bytes(raw[1:], 'big')
        y = pow((x * x * x + 7) % P, (P + 1) // 4, P)
        if y % 2 != raw[0] % 2:
            y = P - y
        point = (x, y)
    else:
        raise VerifyError(f'{len(raw)} bytes is not a secp256k1 point')
    if not _on_curve(point):
        raise VerifyError('point is not on secp256k1')
    return point


def _encode_point(point) -> str:
    return '0x04' + point[0].to_bytes(32, 'big').hex() + point[1].to_bytes(32, 'big').hex()


def _challenge(parts: List[Any], context: str = '') -> int:
    """Fiat-Shamir: the verifier's coin, derived from everything it depends on.

    The transcript includes every public point *and* the context string, so a
    proof of one statement cannot be replayed as a proof of another — the
    mistake that has broken more sigma protocols in the wild than bad math.
    """
    hasher = hashlib.sha256()
    hasher.update(b'0xprof/sigma/v1')
    for part in parts:
        raw = _bytes(part) if not isinstance(part, tuple) else (
            part[0].to_bytes(32, 'big') + part[1].to_bytes(32, 'big'))
        hasher.update(len(raw).to_bytes(4, 'big') + raw)
    context_bytes = (context or '').encode()
    hasher.update(len(context_bytes).to_bytes(4, 'big') + context_bytes)
    return int.from_bytes(hasher.digest(), 'big') % N


def schnorr(proof: Dict[str, Any], vkey: Dict[str, Any],
            public_signals: List[Any]) -> Dict[str, Any]:
    """"I know x with P = xG", and nothing about x.

        s*G == R + c*P,  c = H(P, R, context)
    """
    statement = vkey or {}
    point_p = _decode_point(statement.get('P') or statement.get('pubkey')
                            or (public_signals or [None])[0])
    point_r = _decode_point(proof['R'])
    s = _int(proof['s']) % N
    context = str(statement.get('context') or proof.get('context') or '')
    c = _challenge([point_p, point_r], context)
    left, right = _pt_mul(G, s), _pt_add(point_r, _pt_mul(point_p, c))
    return {
        'ok': left is not None and left == right,
        'detail': {'curve': 'secp256k1', 'check': 'sG == R + cP',
                   'context': context, 'challenge': hex(c)},
    }


def dleq(proof: Dict[str, Any], vkey: Dict[str, Any],
         public_signals: List[Any]) -> Dict[str, Any]:
    """Chaum-Pedersen: A = xG and B = xH share the x, which stays secret.

    The workhorse behind verifiable shuffles, VRFs and key rotation proofs.
    """
    statement = vkey or {}
    base_g = _decode_point(statement.get('G', _encode_point(G)))
    base_h = _decode_point(statement['H'])
    point_a, point_b = _decode_point(statement['A']), _decode_point(statement['B'])
    r1, r2 = _decode_point(proof['R1']), _decode_point(proof['R2'])
    s = _int(proof['s']) % N
    context = str(statement.get('context') or proof.get('context') or '')
    c = _challenge([base_g, base_h, point_a, point_b, r1, r2], context)
    ok = (_pt_mul(base_g, s) == _pt_add(r1, _pt_mul(point_a, c))
          and _pt_mul(base_h, s) == _pt_add(r2, _pt_mul(point_b, c)))
    return {'ok': bool(ok),
            'detail': {'curve': 'secp256k1', 'check': 'sG == R1 + cA and sH == R2 + cB',
                       'context': context, 'challenge': hex(c)}}


def pedersen(proof: Dict[str, Any], vkey: Dict[str, Any],
             public_signals: List[Any]) -> Dict[str, Any]:
    """An opening of C = vG + rH.

    Hiding until opened, binding after — but opening it reveals v, so this is
    a commitment check and not a zero-knowledge proof, and the registry labels
    it that way. It earns its place because commitments are what the zk proofs
    in this market are usually *about*: the threshold circuit proves a fact
    about the value inside one of these.
    """
    statement = vkey or {}
    commitment = _decode_point(statement.get('C') or statement.get('commitment'))
    base_h = _decode_point(statement['H'])
    value, blinding = _int(proof['v']) % N, _int(proof['r']) % N
    recomputed = _pt_add(_pt_mul(G, value), _pt_mul(base_h, blinding))
    return {'ok': recomputed == commitment,
            'detail': {'curve': 'secp256k1', 'check': 'C == vG + rH',
                       'zero_knowledge': False,
                       'recomputed': _encode_point(recomputed) if recomputed else None}}


VERIFIERS = {'groth16': groth16, 'merkle': merkle, 'schnorr': schnorr,
             'dleq': dleq, 'pedersen': pedersen}


def supports(system: str) -> bool:
    return system in VERIFIERS


def verify(system: str, proof: Dict[str, Any], vkey: Optional[Dict[str, Any]],
           public_signals: Optional[List[Any]] = None) -> Dict[str, Any]:
    verifier = VERIFIERS.get(system)
    if not verifier:
        raise VerifyError(f'native cannot verify {system}')
    return verifier(proof or {}, vkey or {}, public_signals or [])


def available() -> Dict[str, Any]:
    """Whether the pairing curves are importable, without importing them twice."""
    try:
        _curve('bn128')
        pairing = True
        note = 'py_ecc — pairings in pure Python, slow and nobody else\'s code'
    except Exception as e:
        pairing = False
        note = f'py_ecc missing, so only the sigma systems verify natively: {e}'
    return {
        'available': True,
        'systems': sorted(VERIFIERS) if pairing else ['merkle', 'schnorr', 'dleq', 'pedersen'],
        'independent': True,
        'note': note,
    }


# ── the prover side, for the systems small enough to prove here ──────
#
# A market with no way to make a proof is a market with nothing to sell on the
# first day. These three are one hash and two multiplications; the snark
# systems are proved by the toolchain in methods/snarkjs.py.

def prove_schnorr(secret: Any, context: str = '', nonce: Optional[int] = None) -> Dict[str, Any]:
    import secrets as _secrets
    x = _int(secret) % N
    if not x:
        raise VerifyError('the secret cannot be zero')
    point_p = _pt_mul(G, x)
    k = (nonce if nonce is not None else _secrets.randbelow(N - 1) + 1) % N
    point_r = _pt_mul(G, k)
    c = _challenge([point_p, point_r], context)
    return {
        'system': 'schnorr',
        'statement': {'P': _encode_point(point_p), 'context': context},
        'proof': {'R': _encode_point(point_r), 's': str((k + c * x) % N)},
        'public_signals': [_encode_point(point_p)],
    }


def prove_dleq(secret: Any, h_point: Any = None, context: str = '',
               nonce: Optional[int] = None) -> Dict[str, Any]:
    import secrets as _secrets
    x = _int(secret) % N
    base_h = _decode_point(h_point) if h_point is not None else _pt_mul(
        G, _challenge([b'0xprof/dleq/H'], context))
    point_a, point_b = _pt_mul(G, x), _pt_mul(base_h, x)
    k = (nonce if nonce is not None else _secrets.randbelow(N - 1) + 1) % N
    r1, r2 = _pt_mul(G, k), _pt_mul(base_h, k)
    c = _challenge([G, base_h, point_a, point_b, r1, r2], context)
    return {
        'system': 'dleq',
        'statement': {'G': _encode_point(G), 'H': _encode_point(base_h),
                      'A': _encode_point(point_a), 'B': _encode_point(point_b),
                      'context': context},
        'proof': {'R1': _encode_point(r1), 'R2': _encode_point(r2),
                  's': str((k + c * x) % N)},
        'public_signals': [_encode_point(point_a), _encode_point(point_b)],
    }


def prove_merkle(leaves: List[Any], index: int, hash_name: str = 'sha256',
                 sorted_pairs: bool = False) -> Dict[str, Any]:
    """Build the tree, keep the path to one leaf, hand back root + path."""
    digest = _hash(hash_name)
    if not leaves:
        raise VerifyError('no leaves')
    if not 0 <= index < len(leaves):
        raise VerifyError(f'leaf {index} is outside 0..{len(leaves) - 1}')
    level = [digest(_bytes(leaf)) for leaf in leaves]
    path, position = [], index
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])            # odd level: duplicate the last
        sibling = level[position ^ 1]
        path.append({'sibling': '0x' + sibling.hex(),
                     'position': 'left' if position % 2 else 'right'})
        level = [digest(*( [level[i] + level[i + 1]] if not sorted_pairs
                           else [b''.join(sorted([level[i], level[i + 1]]))] ))
                 for i in range(0, len(level), 2)]
        position //= 2
    return {
        'system': 'merkle',
        'statement': {'root': '0x' + level[0].hex(), 'hash': hash_name,
                      'sorted': sorted_pairs, 'leaves': len(leaves)},
        'proof': {'leaf': _bytes(leaves[index]).hex() and '0x' + _bytes(leaves[index]).hex(),
                  'path': path},
        'public_signals': ['0x' + _bytes(leaves[index]).hex()],
    }
