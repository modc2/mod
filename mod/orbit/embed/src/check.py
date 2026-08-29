"""A second opinion, from a runtime this module did not write.

Everything else here is home-made: the protobuf writer, the graph runner, the
quantizer. Home-made code that is wrong in the same way twice looks correct —
if `runtime.py` misreads an attribute that `onnxfile.py` also mis-writes, every
test in this module still passes and the file is still broken for everyone else.

So when `onnxruntime` is installed, this module hands it the same file and
compares the numbers. onnxruntime shares no code with anything here; agreement
to a few decimal places means the file really is ONNX and the interpreter really
does implement these operators. Disagreement means one of us is wrong and it is
probably not the reference implementation.

    pip install onnxruntime          # optional, only for this file
    m embed/check name=bow-64

It is optional on purpose. Nothing else in the module imports it, and a box
without it loses this cross-check and nothing else.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from . import evaluate, onnxfile, runtime, text, zoo


def available() -> Dict[str, Any]:
    try:
        import onnxruntime
        return {'available': True, 'version': onnxruntime.__version__}
    except ImportError:
        return {'available': False,
                'install': 'pip install onnxruntime — optional, only for cross-checking'}


def _session(path: str | Path):
    import onnxruntime
    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3
    return onnxruntime.InferenceSession(str(path), options,
                                        providers=['CPUExecutionProvider'])


def feeds_for(name: str) -> Dict[str, np.ndarray]:
    """A representative input for each model this module builds."""
    sentence = 'pour the water slowly over the ground coffee'
    if name.startswith('bow'):
        return {'input_ids': text.token_ids(sentence, zoo.BOW_VOCAB, True)}
    if name.startswith('sent'):
        return {'features': np.stack([text.bag(sentence, zoo.MLP_VOCAB)])}
    raise KeyError(f'no sample input wired up for {name!r}')


def check(name: str = 'bow-64', path: Optional[str] = None,
          tolerance: float = 1e-5) -> Dict[str, Any]:
    """Run one model both ways and compare, tensor by tensor."""
    state = available()
    if not state['available']:
        return {'ok': None, 'reason': 'onnxruntime is not installed', **state}

    file = Path(path) if path else zoo.ensure(name)
    model = onnxfile.load(file)
    feeds = feeds_for(name)

    session = _session(file)
    reference = session.run(None, {k: v for k, v in feeds.items()})
    ours = runtime.run(model, feeds)
    names = [o.name for o in session.get_outputs()]

    comparisons = []
    worst = 0.0
    for output_name, theirs in zip(names, reference):
        mine = np.asarray(ours[output_name])
        gap = float(np.abs(mine.astype(np.float64)
                           - np.asarray(theirs, dtype=np.float64)).max())
        worst = max(worst, gap)
        comparisons.append({'output': output_name, 'shape': list(mine.shape),
                            'max_abs_difference': gap, 'within_tolerance': gap <= tolerance})
    return {
        'ok': worst <= tolerance,
        'model': name, 'path': str(file), 'onnxruntime': state['version'],
        'tolerance': tolerance, 'worst_difference': worst,
        'outputs': comparisons,
    }


def check_all(tolerance: float = 1e-5) -> Dict[str, Any]:
    """Every model, and every compressed version of it, through both runtimes.

    This is the test that matters for the compressor: a quantized file that only
    this module can read is not a compressed model, it is a private format.
    """
    from . import compress as compressor

    state = available()
    if not state['available']:
        return {'ok': None, 'reason': 'onnxruntime is not installed', **state}

    rows: List[Dict[str, Any]] = []
    for name in ('bow-64', 'sent-mlp'):
        source = zoo.ensure(name)
        rows.append({'model': name, 'method': 'float32 (as built)',
                     **_slim(check(name, tolerance=tolerance))})
        for method in compressor.METHODS:
            if method == 'float32':
                continue
            target = source.with_name(f'{source.stem}.check-{method}.onnx')
            compressor.compress_file(source, target, method)
            try:
                rows.append({'model': name, 'method': method,
                             **_slim(check(name, path=str(target), tolerance=tolerance))})
            finally:
                target.unlink(missing_ok=True)
    return {'ok': all(r['ok'] for r in rows), 'tolerance': tolerance,
            'onnxruntime': state['version'], 'results': rows}


def _slim(result: Dict[str, Any]) -> Dict[str, Any]:
    return {'ok': bool(result.get('ok')),
            'worst_difference': result.get('worst_difference'),
            'error': result.get('error')}
