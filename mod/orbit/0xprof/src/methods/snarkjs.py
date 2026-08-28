"""
The snarkjs method — iden3's reference implementation, asked the same question.

This is the code that produced most of the proofs in the world that this module
will ever see, so when it and the native verifier agree, they agree across two
implementations, two languages and two authors. When they disagree the proof is
marked disputed and a human gets to find out which one is wrong; that is the
outcome this method exists to make possible.

It also proves: snarkjs is the only thing here that can turn a witness into a
groth16 / plonk / fflonk proof, so `prove()` is the market's supply side.

    node src/methods/verify.mjs   <  {system, proof, vkey, publicSignals}
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = Path(__file__).resolve().parent / 'verify.mjs'
CLI = ROOT / 'node_modules' / '.bin' / 'snarkjs'
SYSTEMS = ('groth16', 'plonk', 'fflonk')
TIMEOUT = float(os.environ.get('ZKPROF_NODE_TIMEOUT', 120))


class SnarkjsError(Exception):
    pass


def node() -> Optional[str]:
    return shutil.which(os.environ.get('ZKPROF_NODE', 'node'))


def installed() -> bool:
    return (ROOT / 'node_modules' / 'snarkjs' / 'package.json').exists()


def version() -> Optional[str]:
    try:
        return json.loads((ROOT / 'node_modules' / 'snarkjs' / 'package.json')
                          .read_text())['version']
    except Exception:
        return None


def supports(system: str) -> bool:
    return system in SYSTEMS and bool(node()) and installed()


def verify(system: str, proof: Dict[str, Any], vkey: Optional[Dict[str, Any]],
           public_signals: Optional[List[Any]] = None) -> Dict[str, Any]:
    if system not in SYSTEMS:
        raise SnarkjsError(f'snarkjs cannot verify {system}')
    binary = node()
    if not binary or not installed():
        raise SnarkjsError('node or snarkjs is missing — `npm install` in the module')

    payload = json.dumps({'system': system, 'proof': proof, 'vkey': vkey,
                          'publicSignals': public_signals or []})
    try:
        done = subprocess.run([binary, str(SCRIPT)], input=payload, text=True,
                              capture_output=True, timeout=TIMEOUT, cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        raise SnarkjsError(f'snarkjs did not answer within {TIMEOUT:.0f}s')

    try:
        answer = json.loads((done.stdout or '').strip() or '{}')
    except json.JSONDecodeError:
        raise SnarkjsError(f'snarkjs said something that is not JSON: '
                           f'{(done.stderr or done.stdout or "")[:300]}')
    if answer.get('malformed'):
        raise SnarkjsError(answer.get('error') or 'malformed proof')
    if 'ok' not in answer:
        raise SnarkjsError(answer.get('error') or 'snarkjs returned no verdict')
    detail = answer.get('detail') or {}
    detail['snarkjs'] = version()
    return {'ok': bool(answer['ok']), 'detail': detail}


def available() -> Dict[str, Any]:
    binary, have = node(), installed()
    return {
        'available': bool(binary and have),
        'systems': list(SYSTEMS) if (binary and have) else [],
        'independent': True,
        'version': version(),
        'note': ('iden3 snarkjs in a subprocess — the reference implementation'
                 if binary and have else
                 f'node={"found" if binary else "missing"}, '
                 f'snarkjs={"installed" if have else "not installed"}; '
                 'run `npm install` in the module directory'),
    }


# ── the supply side ──────────────────────────────────────────────────

def prove(zkey: bytes, wasm: bytes, inputs: Dict[str, Any],
          system: str = 'groth16') -> Dict[str, Any]:
    """Witness in, proof out — the one thing only the toolchain can do.

    The circuit's compiled wasm and its proving key are passed as bytes so the
    caller can hold them wherever it likes (they arrive here from the store,
    under their own hashes). Both are large and the files are temporary: a
    proving key is not something this module keeps a private copy of.
    """
    if system not in SYSTEMS:
        raise SnarkjsError(f'snarkjs cannot prove {system}')
    binary = node()
    if not binary or not CLI.exists():
        raise SnarkjsError('the snarkjs CLI is missing — `npm install` in the module')

    with tempfile.TemporaryDirectory(prefix='0xprof-prove-') as work:
        work_path = Path(work)
        (work_path / 'circuit.zkey').write_bytes(zkey)
        (work_path / 'circuit.wasm').write_bytes(wasm)
        (work_path / 'input.json').write_text(json.dumps(inputs))
        done = subprocess.run(
            [binary, str(CLI), system, 'fullprove', str(work_path / 'input.json'),
             str(work_path / 'circuit.wasm'), str(work_path / 'circuit.zkey'),
             str(work_path / 'proof.json'), str(work_path / 'public.json')],
            capture_output=True, text=True, timeout=TIMEOUT * 4, cwd=str(ROOT))
        if not (work_path / 'proof.json').exists():
            raise SnarkjsError(
                f'proving failed: {(done.stderr or done.stdout or "")[-400:]}')
        return {
            'system': system,
            'proof': json.loads((work_path / 'proof.json').read_text()),
            'public_signals': json.loads((work_path / 'public.json').read_text()),
        }


def export_vkey(zkey: bytes) -> Dict[str, Any]:
    """The verification key hiding inside a proving key.

    Sellers upload zkeys; buyers need vkeys. Deriving one from the other here
    means a listing cannot ship a verification key that belongs to a different
    circuit than the one it proves against.
    """
    binary = node()
    if not binary or not CLI.exists():
        raise SnarkjsError('the snarkjs CLI is missing')
    with tempfile.TemporaryDirectory(prefix='0xprof-vkey-') as work:
        work_path = Path(work)
        (work_path / 'circuit.zkey').write_bytes(zkey)
        subprocess.run([binary, str(CLI), 'zkey', 'export', 'verificationkey',
                        str(work_path / 'circuit.zkey'), str(work_path / 'vkey.json')],
                       capture_output=True, text=True, timeout=TIMEOUT, cwd=str(ROOT))
        target = work_path / 'vkey.json'
        if not target.exists():
            raise SnarkjsError('that does not look like a zkey')
        return json.loads(target.read_text())
