"""Every model the `onnx` package ships for its own conformance suite.

This is the zoo that costs nothing: ~1,900 graphs already on this disk the
moment `pip install onnx` ran — one per operator and variant (`node/`, the bulk
of it), whole networks re-exported from PyTorch (`pytorch-converted/`), single
PyTorch operators (`pytorch-operator/`), hand-written graphs (`simple/`), and
the `light/` versions of the classic CNNs (AlexNet, DenseNet, Inception,
ResNet-50, ShuffleNet, SqueezeNet, VGG-19, ZFNet) with their weights stripped
down to fit a wheel.

Local-first by construction: no network is touched to list these or to plant
one, so the catalog is never empty even on an air-gapped box.
"""

import glob
import os

from .base import Source, ZooError, entry

_KINDS = {
    'node': ('operator', 'one ONNX operator, exactly as the spec tests it'),
    'pytorch-converted': ('vision', 'a whole PyTorch network, exported'),
    'pytorch-operator': ('operator', 'one PyTorch operator, exported'),
    'simple': ('other', 'a hand-written graph'),
    'light': ('vision', 'a classic CNN, weights shrunk to fit the wheel'),
    'real': ('vision', 'a real network, fetched by the test runner'),
}


def root():
    import onnx
    return os.path.join(os.path.dirname(onnx.__file__), 'backend', 'test', 'data')


class OnnxTests(Source):
    name = 'onnx-tests'
    title = 'ONNX conformance suite'
    kind = 'local'
    home = 'https://github.com/onnx/onnx/tree/main/onnx/backend/test/data'
    note = ('ships inside the onnx wheel — every operator, plus networks '
            'exported from PyTorch. No network, ever.')

    def scrape(self, job, state):
        import onnx
        base = root()
        if not os.path.isdir(base):
            raise ZooError(f'this onnx {onnx.__version__} ships no test data', 404)
        rows = []
        for kind, (domain, what) in _KINDS.items():
            d = os.path.join(base, kind)
            if not os.path.isdir(d):
                continue
            paths = sorted(glob.glob(os.path.join(d, '*', 'model.onnx'))
                           + glob.glob(os.path.join(d, '*.onnx')))
            for p in paths:
                rel = os.path.relpath(p, base)
                case = (os.path.basename(os.path.dirname(p))
                        if p.endswith('/model.onnx') else os.path.basename(p)[:-5])
                label = case[5:] if case.startswith('test_') else case
                op = label.split('_')[0] if kind == 'node' else None
                rows.append(entry(
                    self.name, rel, name=label,
                    files=[[os.path.basename(p), os.path.getsize(p)]],
                    task=f'{kind}' + (f':{op}' if op else ''),
                    domain=domain, author='onnx', license='apache-2.0',
                    tags=[kind] + ([op] if op else []),
                    about=what, local=True,
                    url=f'{self.home}/{os.path.dirname(rel) if rel.endswith("/model.onnx") else rel}',
                    onnx_version=onnx.__version__))
        yield rows, {}, True, len(rows)

    def fetch(self, e, path, job=None, max_bytes=None):
        p = os.path.join(root(), e['ref'])
        if not os.path.isfile(p):
            raise ZooError(f'{p} is gone — was onnx upgraded? rescrape', 404)
        with open(p, 'rb') as f:
            return f.read()
