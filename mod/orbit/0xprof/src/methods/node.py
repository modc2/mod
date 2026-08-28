"""
The node method — the sigma protocols and Merkle trees, checked a second time.

Every system needs at least two verifiers or nothing about it can ever be more
than `claimed`. The snark systems have three or four; the small systems had
one, so this is the second: `src/methods/sigma.mjs`, secp256k1 in BigInt,
written from the protocol description rather than from the Python.

It shares a runtime with the snarkjs method and nothing else — different
language, different arithmetic, different author. That is the property that
makes agreement mean something, and it is the only property that does.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = Path(__file__).resolve().parent / 'sigma.mjs'
SYSTEMS = ('schnorr', 'dleq', 'pedersen', 'merkle')
TIMEOUT = float(os.environ.get('ZKPROF_NODE_TIMEOUT', 60))


class NodeError(Exception):
    pass


def node() -> Optional[str]:
    return shutil.which(os.environ.get('ZKPROF_NODE', 'node'))


def supports(system: str) -> bool:
    return system in SYSTEMS and bool(node())


def verify(system: str, proof: Dict[str, Any], vkey: Optional[Dict[str, Any]],
           public_signals: Optional[List[Any]] = None) -> Dict[str, Any]:
    if system not in SYSTEMS:
        raise NodeError(f'node cannot verify {system}')
    binary = node()
    if not binary:
        raise NodeError('node is not installed')
    payload = json.dumps({'system': system, 'proof': proof, 'statement': vkey or {},
                          'publicSignals': public_signals or []})
    try:
        done = subprocess.run([binary, str(SCRIPT)], input=payload, text=True,
                              capture_output=True, timeout=TIMEOUT, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        raise NodeError(f'node did not answer within {TIMEOUT:.0f}s')
    try:
        answer = json.loads((done.stdout or '').strip() or '{}')
    except json.JSONDecodeError:
        raise NodeError(f'node said something that is not JSON: '
                        f'{(done.stderr or done.stdout or "")[:300]}')
    if answer.get('malformed') or 'ok' not in answer:
        raise NodeError(answer.get('error') or 'no verdict')
    return {'ok': bool(answer['ok']), 'detail': answer.get('detail') or {}}


def available() -> Dict[str, Any]:
    binary = node()
    return {
        'available': bool(binary),
        'systems': list(SYSTEMS) if binary else [],
        'independent': True,
        'note': ('secp256k1 and the tree walk re-implemented in BigInt — the '
                 'second opinion the small systems would otherwise never get'
                 if binary else 'node is not installed'),
    }
