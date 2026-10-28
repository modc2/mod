"""The complexity meter, and the artifacts it reads complexity out of.

Every number in here is also in contracts/ZKNet.sol, computed the same way
with the same integer operations. That is not duplication for its own sake:
the contract is the thing that mints, so the module may not be allowed to
quote a figure the contract would not honour. tests/test_parity.py runs both
against a public EVM and asserts they agree bit for bit, and any change to
the formula that does not land in both files should fail that test.

The meter's inputs sort into two piles, and the whole design hangs off the
difference:

    BOUND       public inputs, proof system, curve. Read off the verification
                key. A prover who changes any of them has a different key,
                which means a different circuit digest and a proof that no
                longer verifies. Nothing to trust.

    MEASURED    the constraint count, when a .r1cs is supplied. This module
                parses the binary header itself, so `measure(r1cs=...)` is
                not taking anybody's word for anything either.

    DECLARED    the constraint count, when it is not. The chain cannot read a
                .r1cs, so on-chain this is a claim, bound to the circuit on
                first use and staked against a challenge window. See the
                contract.

`weigh()` says which of the three it used, always. A caller that wants only
trustworthy numbers can look at `source` and refuse the rest.
"""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

WAD = 10**18

# ── constants, mirroring ZKNet.sol ───────────────────────────────────────
INPUT_WEIGHT = 8
BASE_CONSTRAINTS = 1024
LG_REF = 20 * WAD

INITIAL_RATE = 10**15          # 0.001 ZKW per work unit, WAD-scaled
EPOCH_WORK = 5 * 10**11 * WAD  # work units per halving
MAX_SUPPLY = 10**9 * WAD       # 1e9 ZKW, the sum of the halvings
MAX_EPOCH = 128

BASE_PRICE = 10**15            # $0.001 at zero supply
PRICE_SLOPE = 10**9            # +1e-9 $/ZKW of supply

DECLARED_BPS = 8000
CHALLENGE_WINDOW = 86400

SYSTEMS = {'groth16': 0, 'plonk': 1, 'fflonk': 2}
CURVES = {'bn128': 0, 'bn254': 0, 'altbn128': 0, 'alt_bn128': 0, 'bls12-381': 1,
          'bls12381': 1, 'bls12_381': 1}

SYSTEM_MULTIPLIER = {0: WAD, 1: 135 * 10**16, 2: 15 * 10**17}
CURVE_MULTIPLIER = {0: WAD, 1: 16 * 10**17}

SYSTEM_NAME = {0: 'groth16', 1: 'plonk', 2: 'fflonk'}
CURVE_NAME = {0: 'bn254', 1: 'bls12-381'}


class MeterError(ValueError):
    """A circuit that cannot be priced, with the reason."""


# ── fixed point ──────────────────────────────────────────────────────────

def msb(x: int) -> int:
    """Index of the most significant set bit; msb(1) == 0."""
    if x <= 0:
        raise MeterError('msb: zero')
    return x.bit_length() - 1


def log2_wad(x: int) -> int:
    """log2 of a WAD-scaled x >= WAD, WAD-scaled.

    The integer part off the top bit, then one bit of fraction per squaring.
    Written as the loop the contract runs rather than as `math.log2`, because
    a float here and integer arithmetic there would disagree in the last
    places and the disagreement would be a mint the contract refuses.
    """
    if x < WAD:
        raise MeterError('log2_wad: x < 1')
    n = msb(x // WAD)
    result = n * WAD

    y = x >> n
    if y == WAD:
        return result

    delta = WAD // 2
    while delta > 0:
        y = (y * y) // WAD
        if y >= 2 * WAD:
            result += delta
            y >>= 1
        delta >>= 1
    return result


# ── the formula ──────────────────────────────────────────────────────────

def measure(constraints: int, public_inputs: int, system: int = 0,
            curve: int = 0) -> int:
    """Work units for a circuit, WAD-scaled.

        C = constraints + 8 * publicInputs + 1024
        W = C * log2(C) / log2(2^20) * systemMul * curveMul

    Superlinear in the constraint count because proving is: the FFTs are
    O(C log C) and dominate at every size anyone deploys. Normalised so a
    2^20-constraint groth16 circuit on bn254 meters at exactly 2^20 work
    units, which is what makes the unit mean anything.

    The +1024 floor is why a one-constraint circuit is not free: checking a
    proof costs the verifier four pairings whatever the statement was.
    """
    constraints, public_inputs = int(constraints), int(public_inputs)
    if constraints < 0 or public_inputs < 0:
        raise MeterError('constraints and public inputs cannot be negative')
    c = constraints + INPUT_WEIGHT * public_inputs + BASE_CONSTRAINTS
    work = (c * log2_wad(c * WAD)) // LG_REF
    work = (work * SYSTEM_MULTIPLIER[int(system)]) // WAD
    work = (work * CURVE_MULTIPLIER[int(curve)]) // WAD
    return work


def epoch_of(cumulative_work: int) -> int:
    return int(cumulative_work) // EPOCH_WORK


def rate_at(epoch: int) -> int:
    """ZKW per work unit at that halving, WAD-scaled."""
    return 0 if epoch >= MAX_EPOCH else INITIAL_RATE >> epoch


def emission_for(work: int, cumulative_work: int, supply: int) -> int:
    """What `work` mints, in wei ZKW, given the network's history.

    The rate is read once at the epoch the claim starts in. A claim that
    straddles a halving is paid at the pre-halving rate rather than split,
    so the mint does not depend on transaction ordering inside a block.
    """
    minted = (int(work) * rate_at(epoch_of(cumulative_work))) // WAD
    supply = int(supply)
    if supply >= MAX_SUPPLY:
        return 0
    if supply + minted > MAX_SUPPLY:
        return MAX_SUPPLY - supply
    return minted


def ask_price(supply: int) -> int:
    """The contract's ask for the next token, WAD-scaled USD."""
    return BASE_PRICE + (int(supply) * PRICE_SLOPE) // WAD


def redeem_value(amount: int, supply: int, reserve: int) -> int:
    """What burning `amount` would actually pay. Zero until somebody funds
    the reserve — the ask is a quote, this is the floor under it."""
    return 0 if not supply else (int(reserve) * int(amount)) // int(supply)


# ── reading the artifacts ────────────────────────────────────────────────

@dataclass
class Circuit:
    """A circuit as the meter sees it."""
    constraints: int
    public_inputs: int
    system: int = 0
    curve: int = 0
    source: str = 'declared'          # measured | bound | declared
    digest: Optional[str] = None      # keccak of the verification key
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            'constraints': self.constraints,
            'public_inputs': self.public_inputs,
            'system': SYSTEM_NAME[self.system],
            'curve': CURVE_NAME[self.curve],
            'constraints_source': self.source,
            'circuit': self.digest,
            **({'detail': self.detail} if self.detail else {}),
        }


def read_r1cs(path) -> Dict[str, Any]:
    """The header of a circom .r1cs, which is where the truth lives.

    Layout: magic 'r1cs', a uint32 version, a uint32 section count, then
    sections of (uint32 type, uint64 length, payload). Section 1 is the
    header. Sections are NOT written in numerical order — circom emits the
    constraints before the header — so this scans for type 1 rather than
    assuming it is first, which is the bug you get for reading the spec and
    not the file.
    """
    data = Path(path).read_bytes()
    if len(data) < 12 or data[:4] != b'r1cs':
        raise MeterError(f'{path}: not an r1cs file (bad magic)')
    version, sections = struct.unpack('<II', data[4:12])

    offset, header = 12, None
    for _ in range(sections):
        if offset + 12 > len(data):
            break
        stype, size = struct.unpack('<IQ', data[offset:offset + 12])
        if stype == 1:
            header = data[offset + 12:offset + 12 + size]
            break
        offset += 12 + size

    if header is None or len(header) < 4:
        raise MeterError(f'{path}: no header section')

    field_size = struct.unpack('<I', header[:4])[0]
    prime = int.from_bytes(header[4:4 + field_size], 'little')
    rest = header[4 + field_size:]
    if len(rest) < 28:
        raise MeterError(f'{path}: truncated header')
    n_wires, n_pub_out, n_pub_in, n_prv_in = struct.unpack('<IIII', rest[:16])
    n_labels = struct.unpack('<Q', rest[16:24])[0]
    n_constraints = struct.unpack('<I', rest[24:28])[0]

    return {
        'version': version,
        'constraints': n_constraints,
        # A circom "public signal" is every public output plus every public
        # input — which is exactly what ends up in the groth16 key's IC, and
        # is the number the meter and the contract both mean.
        'public_signals': n_pub_out + n_pub_in,
        'public_outputs': n_pub_out,
        'public_inputs': n_pub_in,
        'private_inputs': n_prv_in,
        'wires': n_wires,
        'labels': n_labels,
        'prime': hex(prime),
        'curve': _curve_of_prime(prime),
        'bytes': len(data),
    }


BN254_R = 21888242871839275222246405745257275088548364400416034343698204186575808495617
BLS12_381_R = 52435875175126190479447740508185965837690552500527637822603658699938581184513


def _curve_of_prime(prime: int) -> str:
    if prime == BN254_R:
        return 'bn254'
    if prime == BLS12_381_R:
        return 'bls12-381'
    return 'unknown'


def read_vkey(vkey: Dict[str, Any]) -> Dict[str, Any]:
    """What a snarkjs verification key says about itself.

    nPublic is not taken from the field of that name — it is taken from
    len(IC) - 1, which is structural. The named field is a comment; the IC
    length is what the pairing check actually uses, and a key whose IC is
    the wrong length will not verify against any proof of that circuit.
    """
    if not isinstance(vkey, dict):
        raise MeterError('verification key must be an object')
    protocol = str(vkey.get('protocol') or 'groth16').lower()
    if protocol not in SYSTEMS:
        raise MeterError(f'unknown proof system {protocol!r}')
    curve = str(vkey.get('curve') or 'bn128').lower()
    if curve not in CURVES:
        raise MeterError(f'unknown curve {curve!r}')

    ic = vkey.get('IC') or vkey.get('ic') or []
    if protocol == 'groth16' and not ic:
        raise MeterError('groth16 key has no IC — cannot count public inputs')

    declared = vkey.get('nPublic')
    public = len(ic) - 1 if ic else int(declared or 0)
    out = {
        'system': SYSTEMS[protocol],
        'curve': CURVES[curve],
        'public_inputs': public,
        'ic_length': len(ic),
    }
    if declared is not None and ic and int(declared) != public:
        # Not fatal — the IC wins — but worth saying out loud, because a key
        # whose own nPublic disagrees with its IC has been edited by hand.
        out['nPublic_mismatch'] = {'declared': int(declared), 'ic_implies': public}
    return out


def canonical_vkey(vkey: Dict[str, Any]) -> Dict[str, Any]:
    """The fields the digest covers, normalised to decimal strings."""
    def g1(p):
        return [str(int(str(v))) for v in p[:2]]

    def g2(p):
        return [[str(int(str(v))) for v in limb[:2]] for limb in p[:2]]

    try:
        return {
            'alpha1': g1(vkey['vk_alpha_1']),
            'beta2': g2(vkey['vk_beta_2']),
            'gamma2': g2(vkey['vk_gamma_2']),
            'delta2': g2(vkey['vk_delta_2']),
            'ic': [g1(p) for p in (vkey.get('IC') or vkey.get('ic') or [])],
        }
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        raise MeterError(f'verification key is missing or malformed: {exc}')


def vkey_fingerprint(vkey: Dict[str, Any]) -> str:
    """A stable id for a key without needing keccak or the EVM ABI.

    This is NOT the contract's circuitDigest — that one is keccak over the
    ABI encoding and is computed in src/evm.py, where the ABI encoder lives.
    This is the module's own index key, and it is sha256 so that the ledger
    can be read on a box with no crypto libraries installed at all.
    """
    blob = json.dumps(canonical_vkey(vkey), sort_keys=True, separators=(',', ':'))
    return 'sha256:' + hashlib.sha256(blob.encode()).hexdigest()


# ── the whole measurement ────────────────────────────────────────────────

def weigh(vkey: Optional[Dict[str, Any]] = None,
          constraints: Optional[int] = None,
          r1cs: Optional[str] = None,
          system: Optional[str] = None,
          curve: Optional[str] = None,
          public_inputs: Optional[int] = None) -> Circuit:
    """Everything the meter can find out about one circuit, and where from.

    Precedence for the constraint count is deliberate: a parsed .r1cs beats
    anything a caller typed, because one of those is a measurement and the
    other is a claim.
    """
    detail: Dict[str, Any] = {}
    sys_id = SYSTEMS.get((system or '').lower(), None)
    curve_id = CURVES.get((curve or '').lower(), None)
    public = public_inputs

    if vkey is not None:
        read = read_vkey(vkey)
        detail['vkey'] = read
        sys_id = read['system'] if sys_id is None else sys_id
        curve_id = read['curve'] if curve_id is None else curve_id
        public = read['public_inputs'] if public is None else public

    source, count = 'declared', constraints
    if r1cs:
        header = read_r1cs(r1cs)
        detail['r1cs'] = header
        count, source = header['constraints'], 'measured'
        if public is None:
            public = header['public_signals']
        if (constraints is not None and int(constraints) != header['constraints']):
            detail['declared_was_wrong'] = {
                'declared': int(constraints),
                'measured': header['constraints'],
                'note': 'the .r1cs header wins; on-chain this is what an '
                        'attestor would file a correction with',
            }

    if count is None:
        raise MeterError(
            'nothing says how big this circuit is: pass constraints=, or an '
            'r1cs= path to have it measured. A groth16 verification key does '
            'not commit to the constraint count, so it cannot be read off the '
            'key however much we would like it to be.')
    if public is None:
        raise MeterError('public_inputs is unknown: pass a verification key, '
                         'an r1cs path, or public_inputs=')

    return Circuit(
        constraints=int(count),
        public_inputs=int(public),
        system=sys_id or 0,
        curve=curve_id or 0,
        source=source,
        digest=vkey_fingerprint(vkey) if vkey else None,
        detail=detail,
    )


def price(circuit: Circuit, cumulative_work: int = 0, supply: int = 0,
          reserve: int = 0) -> Dict[str, Any]:
    """Work, tokens and dollars for a circuit against a given network state.

    Two dollar figures, always both, because they mean different things and
    only one of them is a promise:

        usd_ask     minted * askPrice(supply) — the contract's own quote for
                    what it would sell those tokens for. Deterministic, and
                    backed by nothing but the curve.
        usd_backed  minted * reserve / supply — what redeeming them would
                    actually pay today. Zero until the reserve is funded, and
                    it says zero rather than hiding.
    """
    work = measure(circuit.constraints, circuit.public_inputs,
                   circuit.system, circuit.curve)
    epoch = epoch_of(cumulative_work)
    rate = rate_at(epoch)
    minted = emission_for(work, cumulative_work, supply)
    ask = ask_price(supply)
    vested = (minted * DECLARED_BPS) // 10000

    return {
        **circuit.as_dict(),
        'work': work,
        'work_units': work / WAD,
        'epoch': epoch,
        'rate': rate,
        'rate_zkw_per_work_unit': rate / WAD,
        'minted': minted,
        'zkw': minted / WAD,
        'liquid': minted - vested,
        'zkw_liquid': (minted - vested) / WAD,
        'vested': vested,
        'zkw_vested': vested / WAD,
        'vesting_note': (
            f'{DECLARED_BPS // 100}% of the mint rests on the constraint '
            f'count and vests after the {CHALLENGE_WINDOW // 3600}h challenge '
            f'window; the rest is liquid at once'
            if circuit.source != 'measured' else
            f'{DECLARED_BPS // 100}% vests after the '
            f'{CHALLENGE_WINDOW // 3600}h challenge window — the constraint '
            f'count was measured here, but the chain still only has the '
            f'declaration'),
        'ask_price': ask,
        'ask_price_usd': ask / WAD,
        'usd_ask': (minted * ask) // WAD / WAD,
        'usd_backed': redeem_value(minted, supply + minted, reserve) / WAD,
        'reserve_usd': reserve / WAD,
        'supply_zkw': supply / WAD,
    }
