"""Every architecture torchvision registers, exported here on demand.

121 models across six families — image classification, detection, semantic
segmentation, video, optical flow and the fbgemm-quantized variants — listed
from torchvision's own model registry, so a torchvision upgrade grows this
without a code change. Nothing is downloaded: weights are random unless
`weights=DEFAULT` is asked for at plant time, which is what makes it
local-first. The graph is real; the numbers are not.

Sizes are known *before* anything is built: every weights enum carries
`num_params`, and a model is refused if its fp32 weights would not fit the
store. That check matters on a small box — vit_h_14 is 632M parameters, and
building it just to find out is a 2.5 GB allocation.

(Named tv.py, not torchvision.py, so `import torchvision` inside it can never
resolve to itself.)
"""

from .base import Source, ZooError, entry

_FAMILY = {
    'ImageClassification': ('image-classification', 'vision'),
    'ObjectDetection': ('object-detection', 'vision'),
    'SemanticSegmentation': ('image-segmentation', 'vision'),
    'VideoClassification': ('video-classification', 'vision'),
    'OpticalFlow': ('optical-flow', 'vision'),
}


def _meta(name):
    import torchvision.models as M
    try:
        w = list(M.get_model_weights(name))[0]
    except Exception:
        return {}, None, None
    meta = dict(w.meta)
    try:
        t = w.transforms()
        kind = type(t).__name__
        crop = getattr(t, 'crop_size', None)
        crop = crop[0] if isinstance(crop, (list, tuple)) else crop
    except Exception:
        kind, crop = None, None
    return meta, kind, crop


def _wrap(model, kind):
    """Adapt each family's forward to tensors-in, tuple-of-tensors-out."""
    import torch
    nn = torch.nn

    class Seg(nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x):
            return self.m(x)['out']

    class Det(nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x):
            out = self.m([x[0]])[0]
            return tuple(out[k] for k in sorted(out))

    class Flow(nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, a, b):
            return self.m(a, b, num_flow_updates=4)[-1]

    if kind == 'SemanticSegmentation':
        return Seg(model)
    if kind == 'ObjectDetection':
        return Det(model)
    if kind == 'OpticalFlow':
        return Flow(model)
    return model


def export(name, weights=None, max_bytes=None):
    import torch
    import torchvision.models as M
    from .builtin import export_torch
    meta, kind, crop = _meta(name)
    n = meta.get('num_params')
    if n and max_bytes and n * 4 > max_bytes:
        raise ZooError(f'{name} is {n / 1e6:.0f}M parameters — ~{n * 4 / 2**20:.0f} MiB '
                       f'as fp32, over the store limit (raise INFER_MAX_BYTES)', 413)
    torch.manual_seed(0)
    kw = {'weights': weights} if weights else {'weights': None}
    if name.startswith('quantized_'):
        kw['quantize'] = False      # the fbgemm-packed graph has no ONNX export;
        #                             the quantizable float twin does
    try:
        model = M.get_model(name, **kw)
    except TypeError:
        kw.pop('quantize', None)
        model = M.get_model(name, **kw)
    model = _wrap(model, kind)
    s = int(crop or 224)
    if kind == 'VideoClassification':
        frames = 16
        inputs, names_in = (torch.randn(1, 3, frames, s, s),), ['video']
    elif kind == 'OpticalFlow':
        inputs, names_in = (torch.randn(1, 3, 128, 128), torch.randn(1, 3, 128, 128)), \
            ['image1', 'image2']
    elif kind == 'ObjectDetection':
        inputs, names_in = (torch.rand(1, 3, 320, 320),), ['images']
    else:
        inputs, names_in = (torch.randn(1, 3, s, s),), ['input']
    if kind == 'ObjectDetection':
        names_out = {'maskrcnn': ['boxes', 'labels', 'masks', 'scores'],
                     'keypointrcnn': ['boxes', 'keypoints', 'keypoints_scores',
                                      'labels', 'scores']}
        names_out = next((v for k, v in names_out.items() if name.startswith(k)),
                         ['boxes', 'labels', 'scores'])
    else:
        names_out = ['output']
    return export_torch(model, inputs, names_in, names_out,
                        opset=17)


class TorchVision(Source):
    name = 'torchvision'
    title = 'torchvision model registry'
    kind = 'build'
    home = 'https://pytorch.org/vision/stable/models.html'
    note = ('every architecture torchvision registers, exported on plant — '
            'random weights unless weights=DEFAULT is asked for')

    def scrape(self, job, state):
        try:
            import torchvision
            import torchvision.models as M
        except ImportError:
            raise ZooError('torchvision is not installed here', 501)
        rows = []
        for n in M.list_models():
            meta, kind, crop = _meta(n)
            task, domain = _FAMILY.get(kind, ('image-classification', 'vision'))
            params = meta.get('num_params')
            metrics = meta.get('_metrics') or {}
            best = next(iter(metrics.values()), {}) if metrics else {}
            rows.append(entry(
                self.name, n, name=n,
                files=[['model.onnx', params * 4 if params else None]],
                task=task, domain=domain, params=params, author='pytorch',
                license='bsd-3-clause', tags=[kind or 'model'] + (['quantized'] if n.startswith('quantized_') else []),
                input=crop, gflops=meta.get('_ops'),
                accuracy=best, paper=meta.get('recipe'),
                url=f'{self.home}#{n}', local=True,
                torchvision=torchvision.__version__))
        yield rows, {}, True, len(rows)

    def fetch(self, e, path, job=None, max_bytes=None, weights=None):
        try:
            return export(e['ref'], weights=weights, max_bytes=max_bytes)
        except ZooError:
            raise
        except Exception as ex:
            raise ZooError(f'torchvision {e["ref"]} did not export: '
                           f'{type(ex).__name__}: {str(ex)[:300]}', 422)
