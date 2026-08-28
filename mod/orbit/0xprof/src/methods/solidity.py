"""
The solidity method — run the verifier contract, without deploying it.

`eth_call` takes an optional third parameter, a state override, in which you
can hand a node code to pretend is at an address. So: render the Solidity
verifier snarkjs would have exported for this verification key, compile it,
inject the runtime bytecode at an unused address on a public chain, and call
`verifyProof` on it. The node executes the real contract — the same one a
rollup or a bridge would have deployed — and returns its boolean.

Nothing is deployed, no gas is spent, no key is needed, and the answer comes
from an EVM implementation nobody involved in this module wrote. It is the
strongest independent check available for plonk and fflonk, which is exactly
where the other methods thin out: snarkjs is otherwise the only thing here
that can verify them, and one implementation agreeing with itself is not a
quorum.

    vkey ──ejs template──▶ Verifier.sol ──solc──▶ runtime bytecode
                                                        │
                             proof ──snarkjs──▶ calldata │
                                                        ▼
                                       eth_call + stateOverride ──▶ 0/1

The compile is cached under the hash of the verification key, because that is
the only input it depends on: same circuit, same contract, forever.
"""
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .evm import RpcError, _rpc, pick

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = Path(__file__).resolve().parent / 'solidity.mjs'
CACHE = Path(os.path.expanduser(os.environ.get('ZKPROF_DIR', '~/.mod/0xprof'))) / 'solidity'
SYSTEMS = ('groth16', 'plonk', 'fflonk')
TIMEOUT = float(os.environ.get('ZKPROF_SOLC_TIMEOUT', 180))
# An address with no code on any chain anyone cares about. The override gives
# it code for the duration of one call and nothing is written anywhere.
SCRATCH = '0x00000000000000000000000000000000000f0f0f'


class SolidityError(Exception):
    pass


def node() -> Optional[str]:
    return shutil.which(os.environ.get('ZKPROF_NODE', 'node'))


def installed() -> bool:
    return (ROOT / 'node_modules' / 'solc' / 'package.json').exists() and \
           (ROOT / 'node_modules' / 'snarkjs' / 'package.json').exists()


def supports(system: str) -> bool:
    return system in SYSTEMS and bool(node()) and installed()


def _keccak(data: bytes) -> bytes:
    from Crypto.Hash import keccak
    return keccak.new(digest_bits=256).update(data).digest()


def build(system: str, vkey: Dict[str, Any], proof: Optional[Dict[str, Any]] = None,
          public_signals: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Render + compile the verifier, and translate the proof into arguments."""
    binary = node()
    if not binary or not installed():
        raise SolidityError('node, snarkjs or solc is missing — `npm install` '
                            'in the module directory')
    payload = json.dumps({'system': system, 'vkey': vkey, 'proof': proof,
                          'publicSignals': [str(s) for s in (public_signals or [])]})
    try:
        done = subprocess.run([binary, str(SCRIPT)], input=payload, text=True,
                              capture_output=True, timeout=TIMEOUT, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        raise SolidityError(f'solc did not finish within {TIMEOUT:.0f}s')
    try:
        answer = json.loads((done.stdout or '').strip() or '{}')
    except json.JSONDecodeError:
        raise SolidityError(f'the compiler wrapper said: '
                            f'{(done.stderr or done.stdout or "")[:300]}')
    if answer.get('error'):
        raise SolidityError(answer['error'][:400])
    return answer


def compiled(system: str, vkey: Dict[str, Any]) -> Dict[str, Any]:
    """The contract for this verification key, compiled at most once."""
    key = hashlib.sha256(json.dumps({'s': system, 'v': vkey}, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()
    cached = CACHE / f'{key}.json'
    try:
        return json.loads(cached.read_text())
    except Exception:
        pass
    built = build(system, vkey)
    built.pop('args', None)
    CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(built))
    return built


def _selector(abi: List[Dict[str, Any]]) -> bytes:
    for entry in abi:
        if entry.get('type') == 'function' and entry.get('name') == 'verifyProof':
            types = ','.join(i['type'] for i in entry['inputs'])
            return _keccak(f'verifyProof({types})'.encode())[:4]
    raise SolidityError('the compiled contract has no verifyProof')


def _flatten(value: Any) -> List[int]:
    if isinstance(value, list):
        out: List[int] = []
        for item in value:
            out.extend(_flatten(item))
        return out
    if isinstance(value, str):
        return [int(value, 16) if value.startswith('0x') else int(value)]
    return [int(value)]


def _encode(abi: List[Dict[str, Any]], args: List[Any]) -> bytes:
    """ABI-encode verifyProof's arguments.

    The three verifiers between them use fixed arrays of uint256 and bytes32
    plus, in plonk's case, one dynamic uint256[]. That is the whole surface, so
    the encoder handles exactly that and refuses anything else rather than
    pretending to be a general ABI coder.
    """
    entry = next(e for e in abi
                 if e.get('type') == 'function' and e.get('name') == 'verifyProof')
    types = [i['type'] for i in entry['inputs']]
    if len(types) != len(args):
        raise SolidityError(f'verifyProof wants {len(types)} arguments, '
                            f'snarkjs produced {len(args)}')
    head, tail = b'', b''
    dynamic_offset = 32 * sum(
        1 if kind.endswith('[]') else len(_flatten(arg))
        for kind, arg in zip(types, args))
    for kind, arg in zip(types, args):
        words = _flatten(arg)
        if kind.endswith('[]'):                       # dynamic: offset + len + data
            head += dynamic_offset.to_bytes(32, 'big')
            tail += len(words).to_bytes(32, 'big')
            tail += b''.join(w.to_bytes(32, 'big') for w in words)
            dynamic_offset += 32 * (1 + len(words))
        else:
            head += b''.join(w.to_bytes(32, 'big') for w in words)
    return head + tail


def verify(system: str, proof: Dict[str, Any], vkey: Optional[Dict[str, Any]],
           public_signals: Optional[List[Any]] = None,
           endpoint: Optional[str] = None) -> Dict[str, Any]:
    if system not in SYSTEMS:
        raise SolidityError(f'no solidity verifier for {system}')
    if not vkey:
        raise SolidityError('a solidity verifier is generated from the '
                            'verification key — there is none here')

    built = build(system, vkey, proof, public_signals)
    contract = compiled(system, vkey)          # cached; same bytecode
    calldata = _selector(contract['abi']) + _encode(contract['abi'], built['args'])

    chosen = {'endpoint': endpoint} if endpoint else pick()
    started = time.time()
    result = _rpc(chosen['endpoint'], 'eth_call', [
        {'to': SCRATCH, 'data': '0x' + calldata.hex()},
        'latest',
        {SCRATCH: {'code': contract['bytecode']}},
    ])
    raw = (result or '0x')[2:]
    if not raw:
        raise RpcError('the node returned nothing — this endpoint probably does '
                       'not support eth_call state overrides. Set ZKPROF_RPC to '
                       'one that does (geth, reth and erigon all do)')
    ok = int(raw, 16) == 1
    return {
        'ok': ok,
        'detail': {
            'contract': contract.get('contract'),
            'solc': contract.get('solc'),
            'runtime_bytes': len(contract['bytecode']) // 2,
            'endpoint': chosen['endpoint'].split('//')[-1],
            'chain_id': chosen.get('chain_id'),
            'how': 'eth_call with a state override — the verifier contract is '
                   'executed by the node, never deployed',
            'calldata_bytes': len(calldata),
            'ms': int((time.time() - started) * 1000),
        },
    }


def available() -> Dict[str, Any]:
    if not node() or not installed():
        return {'available': False, 'systems': [], 'independent': True,
                'note': 'needs node with snarkjs and solc — `npm install` in the '
                        'module directory'}
    try:
        chosen = pick()
    except Exception as e:
        return {'available': False, 'systems': [], 'independent': True,
                'note': f'no reachable RPC ({e})'}
    return {
        'available': True,
        'systems': list(SYSTEMS),
        'independent': True,
        'endpoint': chosen['endpoint'],
        'chain_id': chosen['chain_id'],
        'note': 'the real Solidity verifier, compiled from the verification key '
                'and run by a public node through an eth_call state override — '
                'no deployment, no gas',
    }
