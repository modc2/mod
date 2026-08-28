"""
The evm method — let an Ethereum node do the pairing.

Every EVM chain carries the alt_bn128 precompiles from EIP-196/197 at
addresses 0x06 (add), 0x07 (scalar mul) and 0x08 (pairing). They are exactly
what an on-chain groth16 verifier contract calls, and `eth_call` reaches them
without deploying anything: the node runs the arithmetic and hands back a
32-byte 1 or 0. No gas is spent, because eth_call is a simulation.

That makes this the most independent method the module has. The other two run
on this box, on code someone here installed; this one runs on a machine nobody
here controls, in an implementation (geth, reth, nethermind — whichever the
endpoint happens to be) written by a fourth set of people. If native, snarkjs
*and* a public Ethereum node all say the same thing about a proof, the
remaining doubt is about the statement, not the verification.

It only speaks groth16 over bn128, because that is what the precompiles do.
It needs network, so it is optional everywhere and never fails a proof by
being unreachable — an unreachable method returns `unavailable`, which is not
a verdict.
"""
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .native import _curve, _g1, _g2, _int, VerifyError

# Public, keyless, and several of them: one endpoint is a single point of
# failure, and the whole claim of this method is that it does not depend on
# anything in particular.
DEFAULT_RPCS = [
    'https://ethereum-rpc.publicnode.com',
    'https://eth.llamarpc.com',
    'https://1rpc.io/eth',
    'https://rpc.ankr.com/eth',
    'https://base-rpc.publicnode.com',
]
FIELD_MODULUS = 21888242871839275222246405745257275088696311157297823662689037894645226208583
ADD = '0x0000000000000000000000000000000000000006'
MUL = '0x0000000000000000000000000000000000000007'
PAIRING = '0x0000000000000000000000000000000000000008'
TIMEOUT = float(os.environ.get('ZKPROF_RPC_TIMEOUT', 12))
_CACHE: Dict[str, Any] = {'endpoint': None, 'checked': 0.0, 'chain_id': None}


class RpcError(Exception):
    """The chain could not be asked. Never on its own a reason to fail a proof."""


def endpoints() -> List[str]:
    configured = os.environ.get('ZKPROF_RPC', '').strip()
    if configured:
        return [url.strip() for url in configured.split(',') if url.strip()]
    return list(DEFAULT_RPCS)


def _rpc(url: str, method: str, params: List[Any]) -> Any:
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method,
                       'params': params}).encode()
    request = urllib.request.Request(
        url, data=body, headers={'content-type': 'application/json',
                                 'user-agent': '0xprof/1.0 (mod)'})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        answer = json.loads(response.read().decode())
    if 'error' in answer:
        raise RpcError(f'{url}: {answer["error"].get("message", answer["error"])}')
    return answer.get('result')


def pick(force: bool = False) -> Dict[str, Any]:
    """The first endpoint that answers, remembered for a minute.

    Re-probing every endpoint on every verification would make this method the
    slowest thing in the module; a minute is short enough that a dead endpoint
    is noticed and long enough that a page of proofs is one probe.
    """
    if not force and _CACHE['endpoint'] and time.time() - _CACHE['checked'] < 60:
        return {'endpoint': _CACHE['endpoint'], 'chain_id': _CACHE['chain_id'],
                'cached': True}
    errors = []
    for url in endpoints():
        try:
            chain_id = int(_rpc(url, 'eth_chainId', []), 16)
        except Exception as e:
            errors.append(f'{url.split("//")[-1]}: {type(e).__name__}')
            continue
        _CACHE.update({'endpoint': url, 'chain_id': chain_id, 'checked': time.time()})
        return {'endpoint': url, 'chain_id': chain_id, 'cached': False}
    raise RpcError('no RPC endpoint answered — ' + '; '.join(errors[:3]))


def _call(url: str, to: str, data: bytes) -> bytes:
    result = _rpc(url, 'eth_call', [{'to': to, 'data': '0x' + data.hex()}, 'latest'])
    text = (result or '0x')[2:]
    return bytes.fromhex(text) if text else b''


def _u256(value: int) -> bytes:
    return int(value).to_bytes(32, 'big')


def _g1_bytes(point) -> bytes:
    """A G1 point in affine form, the way EIP-196 wants it: x then y."""
    from py_ecc import optimized_bn128 as bn
    if point is None or bn.is_inf(point):
        return _u256(0) + _u256(0)
    x, y = bn.normalize(point)
    return _u256(int(x)) + _u256(int(y))


def _g2_bytes(point) -> bytes:
    """G2 in EIP-197 order: imaginary part first, for both coordinates.

    This is the single most common way to get an on-chain verifier wrong.
    snarkjs writes [c0, c1] (real, imaginary); the precompile reads
    (x_imag, x_real, y_imag, y_real).
    """
    from py_ecc import optimized_bn128 as bn
    x, y = bn.normalize(point)
    xc = x.coeffs
    yc = y.coeffs
    return _u256(int(xc[1])) + _u256(int(xc[0])) + _u256(int(yc[1])) + _u256(int(yc[0]))


def supports(system: str) -> bool:
    return system == 'groth16'


def verify(system: str, proof: Dict[str, Any], vkey: Optional[Dict[str, Any]],
           public_signals: Optional[List[Any]] = None,
           onchain_msm: Optional[bool] = None) -> Dict[str, Any]:
    """Groth16, checked by a node that has never heard of this module.

    `onchain_msm` decides whether the public-input combination is also done on
    the chain (one eth_call per signal, via 0x07 and 0x06) or locally. On by
    default for small statements, because doing it locally would mean this
    method still trusted this box's arithmetic for part of the answer.
    """
    if system != 'groth16':
        raise RpcError(f'the precompiles do not verify {system}')
    curve_name = (vkey or {}).get('curve', 'bn128').lower()
    if curve_name not in ('bn128', 'bn254', 'altbn128'):
        raise RpcError(f'the evm precompiles are bn128 only, this key is {curve_name}')

    from py_ecc import optimized_bn128 as bn
    chosen = pick()
    url = chosen['endpoint']
    ic = (vkey or {}).get('IC') or []
    signals = [_int(s) for s in (public_signals or [])]
    if len(ic) != len(signals) + 1:
        raise VerifyError(f'key expects {len(ic) - 1} public signals, got {len(signals)}')

    if onchain_msm is None:
        onchain_msm = len(signals) <= 8
    calls = 0

    if onchain_msm:
        # vk_x = IC[0] + Σ signal_i · IC[i+1], every step on the chain.
        accumulator = _g1_bytes(_g1(bn, ic[0]))
        for signal, point in zip(signals, ic[1:]):
            product = _call(url, MUL, _g1_bytes(_g1(bn, point)) + _u256(signal))
            calls += 1
            if len(product) != 64:
                raise RpcError('ecMul precompile returned nothing — wrong chain?')
            accumulator = _call(url, ADD, accumulator + product)
            calls += 1
            if len(accumulator) != 64:
                raise RpcError('ecAdd precompile returned nothing')
        vk_x_bytes = accumulator
    else:
        vk_x = _g1(bn, ic[0])
        for signal, point in zip(signals, ic[1:]):
            vk_x = bn.add(vk_x, bn.multiply(_g1(bn, point), signal))
        vk_x_bytes = _g1_bytes(vk_x)

    pairs = (
        _g1_bytes(bn.neg(_g1(bn, proof['pi_a']))) + _g2_bytes(_g2(bn, proof['pi_b'])) +
        _g1_bytes(_g1(bn, vkey['vk_alpha_1'])) + _g2_bytes(_g2(bn, vkey['vk_beta_2'])) +
        vk_x_bytes + _g2_bytes(_g2(bn, vkey['vk_gamma_2'])) +
        _g1_bytes(_g1(bn, proof['pi_c'])) + _g2_bytes(_g2(bn, vkey['vk_delta_2']))
    )
    started = time.time()
    result = _call(url, PAIRING, pairs)
    calls += 1
    if len(result) != 32:
        raise RpcError('the pairing precompile rejected the input — '
                       'malformed points, or an endpoint that is not an EVM node')
    ok = int.from_bytes(result, 'big') == 1
    return {
        'ok': ok,
        'detail': {
            'endpoint': url.split('//')[-1],
            'chain_id': chosen['chain_id'],
            'precompiles': ['0x08 ecPairing'] + (['0x07 ecMul', '0x06 ecAdd']
                                                 if onchain_msm else []),
            'eth_calls': calls,
            'public_inputs_combined': 'on-chain' if onchain_msm else 'locally',
            'ms': int((time.time() - started) * 1000),
            'gas_if_onchain': 45000 + 34000 * 4,
        },
    }


def available() -> Dict[str, Any]:
    try:
        chosen = pick()
        return {'available': True, 'systems': ['groth16'], 'independent': True,
                'endpoint': chosen['endpoint'], 'chain_id': chosen['chain_id'],
                'note': 'alt_bn128 precompiles via eth_call — no contract, no gas'}
    except Exception as e:
        return {'available': False, 'systems': [], 'independent': True,
                'note': f'no reachable RPC ({e}) — set ZKPROF_RPC to endpoints '
                        'this box can reach; the method stays unavailable, '
                        'which is not a verdict'}
