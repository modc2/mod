"""
The registry — what a proof system is, in enough detail to price one.

Every system here answers four questions, because a marketplace that cannot
answer them is selling mystery: what does a proof of this kind *claim*, what
does the verifier need in order to check it, does checking it reveal anything,
and does the system require a trusted setup somebody could have cheated.

`zero_knowledge: False` entries are not an oversight. Merkle inclusion and
Pedersen openings are the majority of what gets called a "proof" in practice,
they verify perfectly well, and they hide nothing. Listing them with the flag
set honestly is better than either pretending they are zk or refusing to carry
them and watching them be sold as groth16 by someone less careful.
"""
from typing import Any, Dict, List

SYSTEMS: Dict[str, Dict[str, Any]] = {
    'groth16': {
        'label': 'Groth16',
        'family': 'zk-SNARK',
        'curves': ['bn128', 'bls12-381'],
        'zero_knowledge': True,
        'succinct': True,
        'trusted_setup': 'per-circuit — a phase-2 ceremony per circuit, on top of a universal phase 1',
        'proof_size': '3 group elements (~200 bytes on bn128)',
        'verifier_cost': '3 pairings + one multi-scalar multiplication over the public inputs',
        'needs': ['proof', 'vkey', 'public_signals'],
        'format': 'snarkjs proof.json / verification_key.json',
        'claims': 'I know a witness satisfying this circuit for these public signals',
        'methods': ['native', 'snarkjs', 'evm', 'solidity', 'browser'],
        'note': 'the smallest proof and the cheapest verifier in wide use, paid for '
                'with a ceremony whose toxic waste you have to believe was destroyed',
    },
    'plonk': {
        'label': 'PLONK',
        'family': 'zk-SNARK',
        'curves': ['bn128'],
        'zero_knowledge': True,
        'succinct': True,
        'trusted_setup': 'universal — one ceremony serves every circuit up to its size',
        'proof_size': '~800 bytes',
        'verifier_cost': '2 pairings plus polynomial evaluation checks',
        'needs': ['proof', 'vkey', 'public_signals'],
        'format': 'snarkjs proof.json / verification_key.json',
        'claims': 'I know a witness satisfying this circuit for these public signals',
        'methods': ['snarkjs', 'solidity', 'browser'],
        'note': 'no per-circuit ceremony, which is why a new circuit can be listed '
                'here the day it is written',
    },
    'fflonk': {
        'label': 'fflonk',
        'family': 'zk-SNARK',
        'curves': ['bn128'],
        'zero_knowledge': True,
        'succinct': True,
        'trusted_setup': 'universal',
        'proof_size': '~1 KB',
        'verifier_cost': 'a single pairing check — the cheapest on-chain of the three',
        'needs': ['proof', 'vkey', 'public_signals'],
        'format': 'snarkjs proof.json / verification_key.json',
        'claims': 'I know a witness satisfying this circuit for these public signals',
        'methods': ['snarkjs', 'solidity', 'browser'],
        'note': 'plonk rearranged to trade prover time for a cheaper verifier',
    },
    'merkle': {
        'label': 'Merkle inclusion',
        'family': 'commitment',
        'curves': [],
        'zero_knowledge': False,
        'succinct': True,
        'trusted_setup': None,
        'proof_size': 'log2(n) hashes',
        'verifier_cost': 'log2(n) hashes',
        'needs': ['proof.leaf', 'proof.path', 'statement.root'],
        'format': '{leaf, path:[{sibling, position}]} against {root, hash, sorted}',
        'claims': 'this leaf is in the set committed to by this root',
        'methods': ['native', 'node', 'browser'],
        'note': 'not zero-knowledge — it reveals the leaf and the path. Carried '
                'because airdrops, allowlists and state proofs are all this shape',
    },
    'schnorr': {
        'label': 'Schnorr (Fiat-Shamir)',
        'family': 'sigma protocol',
        'curves': ['secp256k1'],
        'zero_knowledge': True,
        'succinct': True,
        'trusted_setup': None,
        'proof_size': '2 group elements',
        'verifier_cost': '2 scalar multiplications',
        'needs': ['proof.R', 'proof.s', 'statement.P'],
        'format': '{R, s} against {P, context}',
        'claims': 'I hold the private key behind this public key',
        'methods': ['native', 'node', 'browser'],
        'note': 'the whole proof fits in a tweet and needs no ceremony; the context '
                'string is bound into the challenge so a proof cannot be replayed '
                'against a different statement',
    },
    'dleq': {
        'label': 'Chaum-Pedersen (DLEQ)',
        'family': 'sigma protocol',
        'curves': ['secp256k1'],
        'zero_knowledge': True,
        'succinct': True,
        'trusted_setup': None,
        'proof_size': '3 group elements',
        'verifier_cost': '4 scalar multiplications',
        'needs': ['proof.R1', 'proof.R2', 'proof.s', 'statement.{G,H,A,B}'],
        'format': '{R1, R2, s} against {G, H, A, B, context}',
        'claims': 'these two points share an exponent, and I know it',
        'methods': ['native', 'node', 'browser'],
        'note': 'what a VRF, a verifiable shuffle or a key-rotation proof is made of',
    },
    'pedersen': {
        'label': 'Pedersen opening',
        'family': 'commitment',
        'curves': ['secp256k1'],
        'zero_knowledge': False,
        'succinct': True,
        'trusted_setup': None,
        'proof_size': '2 scalars',
        'verifier_cost': '2 scalar multiplications',
        'needs': ['proof.v', 'proof.r', 'statement.{C,H}'],
        'format': '{v, r} against {C, H}',
        'claims': 'this commitment opens to this value',
        'methods': ['native', 'node', 'browser'],
        'note': 'opening reveals the value, so this is binding, not hiding — it is '
                'here because it is what the zk proofs above are usually *about*',
    },
}

ALIASES = {'groth': 'groth16', 'g16': 'groth16', 'snark': 'groth16',
           'plonk-bn128': 'plonk', 'chaum-pedersen': 'dleq', 'merkle-tree': 'merkle',
           'inclusion': 'merkle', 'sigma': 'schnorr'}


def resolve(name: str) -> str:
    key = (name or '').strip().lower().replace('_', '-')
    key = ALIASES.get(key, key)
    if key not in SYSTEMS:
        raise KeyError(f'unknown proof system {name!r} — one of {", ".join(sorted(SYSTEMS))}')
    return key


def get(name: str) -> Dict[str, Any]:
    key = resolve(name)
    return {'system': key, **SYSTEMS[key]}


def names() -> List[str]:
    return sorted(SYSTEMS)


def catalog() -> List[Dict[str, Any]]:
    return [get(name) for name in names()]


def sniff(payload: Dict[str, Any]) -> str:
    """Guess the system from the shape of what was uploaded.

    Proof files in the wild are rarely labelled by whoever exports them, and a
    market that makes you pick from a dropdown before it will look at your file
    loses uploads. The guess is only ever a default the caller can override.
    """
    proof = payload.get('proof') if isinstance(payload.get('proof'), dict) else payload
    vkey = payload.get('vkey') or payload.get('verification_key') or {}
    for source in (proof, vkey):
        declared = (source or {}).get('protocol')
        if isinstance(declared, str) and declared.lower() in SYSTEMS:
            return declared.lower()
    if {'pi_a', 'pi_b', 'pi_c'} <= set(proof or {}):
        return 'groth16'
    if 'polynomials' in (proof or {}) and 'evaluations' in (proof or {}):
        return 'fflonk'
    if {'A', 'B', 'C', 'Z', 'Wxi'} <= set(proof or {}):
        return 'plonk'
    if 'path' in (proof or {}) or 'siblings' in (proof or {}):
        return 'merkle'
    if {'R1', 'R2', 's'} <= set(proof or {}):
        return 'dleq'
    if {'R', 's'} <= set(proof or {}):
        return 'schnorr'
    if {'v', 'r'} <= set(proof or {}):
        return 'pedersen'
    raise KeyError('cannot tell what kind of proof this is — pass `system`')
