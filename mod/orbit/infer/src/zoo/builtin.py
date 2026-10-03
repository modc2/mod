"""Every architecture family, built here — one small model each.

Not a download: the graph is constructed on this box, so it works offline and
is identical every time (each build is seeded, and the store is keyed on the
SHA-256 of the bytes, so planting twice lands on the same id). Weights are
random — these exist to show what the passes do to each *shape* of network,
not to classify anything.

Two builders. `torch` ones cover every layer family a real model is made of —
dense, conv 1/2/3-D, depthwise, transposed conv, recurrent, attention, MoE,
graph, recommender, diffusion — and export through TorchScript. `onnx` ones are
written straight in onnx.helper and need nothing but the onnx package: the
classical-ML operators (`ai.onnx.ml`), control flow, and the smallest possible
graphs, so a box without torch still has something to plant.
"""

import os
import tempfile
import warnings

from .base import Source, ZooError, entry

# name → (builder, task, domain, what). Filled by the two decorators below.
ARCHS = {}


def _torch_arch(name, task, domain, what):
    def deco(fn):
        ARCHS[name] = ('torch', fn, task, domain, what)
        return fn
    return deco


def _onnx_arch(name, task, domain, what):
    def deco(fn):
        ARCHS[name] = ('onnx', fn, task, domain, what)
        return fn
    return deco


# ── torch builders: each returns (module, inputs, input_names, output_names)

def _t():
    import torch
    return torch, torch.nn, torch.nn.functional


@_torch_arch('mlp', 'tabular-classification', 'tabular',
             'feed-forward net holding BatchNorm the fuser can eat')
def _mlp():
    torch, nn, _ = _t()
    m = nn.Sequential(nn.Linear(64, 512), nn.BatchNorm1d(512), nn.ReLU(),
                      nn.Linear(512, 512), nn.BatchNorm1d(512), nn.ReLU(),
                      nn.Linear(512, 10))
    return m, (torch.randn(8, 64),), ['input'], ['output']


@_torch_arch('cnn', 'image-classification', 'vision',
             'two conv blocks with BatchNorm, pooled into a linear head')
def _cnn():
    torch, nn, _ = _t()

    class CNN(nn.Module):
        def __init__(self):
            super().__init__()
            self.body = nn.Sequential(
                nn.Conv2d(3, 32, 3, padding=1), nn.BatchNorm2d(32), nn.ReLU(),
                nn.MaxPool2d(2),
                nn.Conv2d(32, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
                nn.AdaptiveAvgPool2d(1))
            self.head = nn.Linear(64, 10)

        def forward(self, x):
            return self.head(self.body(x).flatten(1))
    return CNN(), (torch.randn(4, 3, 32, 32),), ['input'], ['output']


@_torch_arch('transformer-block', 'feature-extraction', 'text',
             'one pre-norm transformer block: attention + GELU MLP')
def _block():
    torch, nn, _ = _t()

    class Block(nn.Module):
        def __init__(self, d=128, heads=4):
            super().__init__()
            self.attn = nn.MultiheadAttention(d, heads, batch_first=True)
            self.n1, self.n2 = nn.LayerNorm(d), nn.LayerNorm(d)
            self.ff = nn.Sequential(nn.Linear(d, 512), nn.GELU(), nn.Linear(512, d))

        def forward(self, x):
            h = self.n1(x)
            x = x + self.attn(h, h, h, need_weights=False)[0]
            return x + self.ff(self.n2(x))
    return Block(), (torch.randn(2, 32, 128),), ['input'], ['output']


@_torch_arch('resnet-block', 'image-classification', 'vision',
             'residual bottleneck stack — the skip-add every ResNet is made of')
def _resnet():
    torch, nn, F = _t()

    class Bottle(nn.Module):
        def __init__(self, c):
            super().__init__()
            self.a = nn.Sequential(nn.Conv2d(c, c // 4, 1), nn.BatchNorm2d(c // 4), nn.ReLU(),
                                   nn.Conv2d(c // 4, c // 4, 3, padding=1),
                                   nn.BatchNorm2d(c // 4), nn.ReLU(),
                                   nn.Conv2d(c // 4, c, 1), nn.BatchNorm2d(c))

        def forward(self, x):
            return F.relu(x + self.a(x))

    m = nn.Sequential(nn.Conv2d(3, 64, 7, 2, 3), nn.BatchNorm2d(64), nn.ReLU(),
                      nn.MaxPool2d(3, 2, 1), Bottle(64), Bottle(64), Bottle(64),
                      nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Linear(64, 100))
    return m, (torch.randn(2, 3, 64, 64),), ['input'], ['output']


@_torch_arch('mobilenet-block', 'image-classification', 'vision',
             'inverted residual: depthwise conv, squeeze-excite, hard-swish')
def _mobile():
    torch, nn, _ = _t()

    class SE(nn.Module):
        def __init__(self, c):
            super().__init__()
            self.f = nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Conv2d(c, c // 4, 1), nn.ReLU(),
                                   nn.Conv2d(c // 4, c, 1), nn.Hardsigmoid())

        def forward(self, x):
            return x * self.f(x)

    class IR(nn.Module):
        def __init__(self, c, e=4):
            super().__init__()
            h = c * e
            self.f = nn.Sequential(nn.Conv2d(c, h, 1), nn.BatchNorm2d(h), nn.Hardswish(),
                                   nn.Conv2d(h, h, 3, padding=1, groups=h), nn.BatchNorm2d(h),
                                   nn.Hardswish(), SE(h), nn.Conv2d(h, c, 1), nn.BatchNorm2d(c))

        def forward(self, x):
            return x + self.f(x)

    m = nn.Sequential(nn.Conv2d(3, 16, 3, 2, 1), nn.BatchNorm2d(16), nn.Hardswish(),
                      IR(16), IR(16), nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Linear(16, 10))
    return m, (torch.randn(2, 3, 64, 64),), ['input'], ['output']


@_torch_arch('convnext-block', 'image-classification', 'vision',
             '7×7 depthwise conv, channels-last LayerNorm, inverted MLP')
def _convnext():
    torch, nn, _ = _t()

    class Blk(nn.Module):
        def __init__(self, c=48):
            super().__init__()
            self.dw = nn.Conv2d(c, c, 7, padding=3, groups=c)
            self.n = nn.LayerNorm(c)
            self.p1, self.p2 = nn.Linear(c, 4 * c), nn.Linear(4 * c, c)

        def forward(self, x):
            y = self.dw(x).permute(0, 2, 3, 1)
            y = self.p2(torch.nn.functional.gelu(self.p1(self.n(y))))
            return x + y.permute(0, 3, 1, 2)

    m = nn.Sequential(nn.Conv2d(3, 48, 4, 4), Blk(), Blk(),
                      nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Linear(48, 10))
    return m, (torch.randn(2, 3, 64, 64),), ['input'], ['output']


@_torch_arch('unet', 'image-segmentation', 'vision',
             'encoder–decoder with skip concats and transposed-conv upsampling')
def _unet():
    torch, nn, _ = _t()

    def blk(i, o):
        return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(),
                             nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU())

    class UNet(nn.Module):
        def __init__(self):
            super().__init__()
            self.e1, self.e2, self.mid = blk(3, 16), blk(16, 32), blk(32, 64)
            self.u2, self.d2 = nn.ConvTranspose2d(64, 32, 2, 2), blk(64, 32)
            self.u1, self.d1 = nn.ConvTranspose2d(32, 16, 2, 2), blk(32, 16)
            self.out = nn.Conv2d(16, 2, 1)
            self.pool = nn.MaxPool2d(2)

        def forward(self, x):
            a = self.e1(x)
            b = self.e2(self.pool(a))
            c = self.mid(self.pool(b))
            b = self.d2(torch.cat([self.u2(c), b], 1))
            a = self.d1(torch.cat([self.u1(b), a], 1))
            return self.out(a)
    return UNet(), (torch.randn(1, 3, 64, 64),), ['input'], ['output']


@_torch_arch('yolo-lite', 'object-detection', 'vision',
             'conv backbone with two heads — boxes and class scores, two outputs')
def _yolo():
    torch, nn, _ = _t()

    class Det(nn.Module):
        def __init__(self, classes=20, anchors=3):
            super().__init__()
            self.b = nn.Sequential(nn.Conv2d(3, 16, 3, 2, 1), nn.SiLU(),
                                   nn.Conv2d(16, 32, 3, 2, 1), nn.SiLU(),
                                   nn.Conv2d(32, 64, 3, 2, 1), nn.SiLU())
            self.box = nn.Conv2d(64, anchors * 4, 1)
            self.cls = nn.Conv2d(64, anchors * classes, 1)
            self.a, self.c = anchors, classes

        def forward(self, x):
            f = self.b(x)
            n = f.shape[0]
            boxes = self.box(f).reshape(n, self.a * 4, -1).transpose(1, 2).reshape(n, -1, 4)
            scores = torch.sigmoid(self.cls(f)).reshape(n, self.a * self.c, -1) \
                .transpose(1, 2).reshape(n, -1, self.c)
            return boxes, scores
    return Det(), (torch.randn(1, 3, 128, 128),), ['images'], ['boxes', 'scores']


@_torch_arch('vit-tiny', 'image-classification', 'vision',
             'vision transformer: patch embedding, class token, two encoder layers')
def _vit():
    torch, nn, _ = _t()

    class ViT(nn.Module):
        def __init__(self, d=96, patch=8, img=32):
            super().__init__()
            self.patch = nn.Conv2d(3, d, patch, patch)
            n = (img // patch) ** 2
            self.cls = nn.Parameter(torch.randn(1, 1, d))
            self.pos = nn.Parameter(torch.randn(1, n + 1, d))
            layer = nn.TransformerEncoderLayer(d, 4, 4 * d, batch_first=True,
                                               activation='gelu', norm_first=True)
            self.enc = nn.TransformerEncoder(layer, 2, enable_nested_tensor=False)
            self.head = nn.Linear(d, 10)

        def forward(self, x):
            p = self.patch(x).flatten(2).transpose(1, 2)
            p = torch.cat([self.cls.expand(p.shape[0], -1, -1), p], 1) + self.pos
            return self.head(self.enc(p)[:, 0])
    return ViT(), (torch.randn(2, 3, 32, 32),), ['pixel_values'], ['logits']


@_torch_arch('mlp-mixer', 'image-classification', 'vision',
             'MLP-Mixer: token-mixing and channel-mixing MLPs, no attention, no conv')
def _mixer():
    torch, nn, _ = _t()

    class Mix(nn.Module):
        def __init__(self, n, d):
            super().__init__()
            self.n1, self.n2 = nn.LayerNorm(d), nn.LayerNorm(d)
            self.tok = nn.Sequential(nn.Linear(n, 2 * n), nn.GELU(), nn.Linear(2 * n, n))
            self.ch = nn.Sequential(nn.Linear(d, 4 * d), nn.GELU(), nn.Linear(4 * d, d))

        def forward(self, x):
            x = x + self.tok(self.n1(x).transpose(1, 2)).transpose(1, 2)
            return x + self.ch(self.n2(x))

    class Mixer(nn.Module):
        def __init__(self):
            super().__init__()
            self.p = nn.Conv2d(3, 64, 8, 8)
            self.b = nn.Sequential(Mix(16, 64), Mix(16, 64))
            self.h = nn.Linear(64, 10)

        def forward(self, x):
            return self.h(self.b(self.p(x).flatten(2).transpose(1, 2)).mean(1))
    return Mixer(), (torch.randn(2, 3, 32, 32),), ['input'], ['output']


@_torch_arch('autoencoder', 'feature-extraction', 'vision',
             'dense encoder to a 16-d bottleneck and back — reconstruction')
def _ae():
    torch, nn, _ = _t()
    m = nn.Sequential(nn.Flatten(), nn.Linear(784, 256), nn.ReLU(), nn.Linear(256, 16),
                      nn.ReLU(), nn.Linear(16, 256), nn.ReLU(), nn.Linear(256, 784),
                      nn.Sigmoid(), nn.Unflatten(1, (1, 28, 28)))
    return m, (torch.randn(4, 1, 28, 28),), ['input'], ['output']


@_torch_arch('gan-generator', 'unconditional-image-generation', 'generative',
             'DCGAN generator: latent vector → image through transposed convs')
def _gan():
    torch, nn, _ = _t()
    m = nn.Sequential(
        nn.Unflatten(1, (64, 1, 1)),
        nn.ConvTranspose2d(64, 128, 4, 1, 0), nn.BatchNorm2d(128), nn.ReLU(),
        nn.ConvTranspose2d(128, 64, 4, 2, 1), nn.BatchNorm2d(64), nn.ReLU(),
        nn.ConvTranspose2d(64, 32, 4, 2, 1), nn.BatchNorm2d(32), nn.ReLU(),
        nn.ConvTranspose2d(32, 3, 4, 2, 1), nn.Tanh())
    return m, (torch.randn(2, 64),), ['latent'], ['image']


@_torch_arch('diffusion-unet', 'text-to-image', 'generative',
             'denoiser with a sinusoidal timestep embedding — two inputs, x and t')
def _diff():
    torch, nn, F = _t()

    class Den(nn.Module):
        def __init__(self, c=32):
            super().__init__()
            self.t = nn.Sequential(nn.Linear(32, c), nn.SiLU(), nn.Linear(c, c))
            self.i = nn.Conv2d(3, c, 3, padding=1)
            self.d = nn.Conv2d(c, 2 * c, 3, 2, 1)
            self.m = nn.Conv2d(2 * c, 2 * c, 3, padding=1)
            self.u = nn.ConvTranspose2d(2 * c, c, 2, 2)
            self.o = nn.Conv2d(2 * c, 3, 3, padding=1)
            self.g1, self.g2 = nn.GroupNorm(8, c), nn.GroupNorm(8, 2 * c)
            self.register_buffer('freq', torch.exp(-torch.arange(16) * 0.5))

        def forward(self, x, t):
            e = t[:, None] * self.freq[None]
            e = self.t(torch.cat([e.sin(), e.cos()], 1))
            a = F.silu(self.g1(self.i(x) + e[:, :, None, None]))
            b = F.silu(self.g2(self.m(self.d(a))))
            return self.o(torch.cat([self.u(b), a], 1))
    return Den(), (torch.randn(2, 3, 32, 32), torch.rand(2)), ['sample', 'timestep'], ['noise']


@_torch_arch('lstm', 'text-classification', 'text',
             'two-layer LSTM over a sequence, last state into a classifier')
def _lstm():
    torch, nn, _ = _t()

    class M(nn.Module):
        def __init__(self):
            super().__init__()
            self.r = nn.LSTM(32, 64, 2, batch_first=True)
            self.h = nn.Linear(64, 5)

        def forward(self, x):
            return self.h(self.r(x)[0][:, -1])
    return M(), (torch.randn(2, 20, 32),), ['input'], ['output']


@_torch_arch('gru', 'time-series-forecasting', 'tabular',
             'GRU forecaster: a window of readings in, the next step out')
def _gru():
    torch, nn, _ = _t()

    class M(nn.Module):
        def __init__(self):
            super().__init__()
            self.r = nn.GRU(8, 48, batch_first=True)
            self.h = nn.Linear(48, 8)

        def forward(self, x):
            return self.h(self.r(x)[0][:, -1])
    return M(), (torch.randn(2, 30, 8),), ['input'], ['output']


@_torch_arch('rnn', 'time-series-forecasting', 'tabular',
             'the plain Elman RNN — tanh recurrence, nothing gated')
def _rnn():
    torch, nn, _ = _t()

    class M(nn.Module):
        def __init__(self):
            super().__init__()
            self.r = nn.RNN(16, 32, batch_first=True)
            self.h = nn.Linear(32, 1)

        def forward(self, x):
            return self.h(self.r(x)[0])
    return M(), (torch.randn(2, 24, 16),), ['input'], ['output']


@_torch_arch('bilstm-tagger', 'token-classification', 'text',
             'embedding + bidirectional LSTM, a label per token (int64 ids in)')
def _tagger():
    torch, nn, _ = _t()

    class M(nn.Module):
        def __init__(self):
            super().__init__()
            self.e = nn.Embedding(1000, 64)
            self.r = nn.LSTM(64, 64, batch_first=True, bidirectional=True)
            self.h = nn.Linear(128, 9)

        def forward(self, ids):
            return self.h(self.r(self.e(ids))[0])
    return M(), (torch.randint(0, 1000, (2, 16)),), ['input_ids'], ['logits']


@_torch_arch('text-cnn', 'text-classification', 'text',
             'Kim CNN: embeddings, parallel 1-D convs of widths 3/4/5, max-pool')
def _textcnn():
    torch, nn, _ = _t()

    class M(nn.Module):
        def __init__(self):
            super().__init__()
            self.e = nn.Embedding(2000, 64)
            self.c = nn.ModuleList([nn.Conv1d(64, 32, k) for k in (3, 4, 5)])
            self.h = nn.Linear(96, 2)

        def forward(self, ids):
            x = self.e(ids).transpose(1, 2)
            return self.h(torch.cat([c(x).relu().amax(2) for c in self.c], 1))
    return M(), (torch.randint(0, 2000, (2, 40)),), ['input_ids'], ['logits']


@_torch_arch('bert-tiny', 'fill-mask', 'text',
             'encoder-only transformer: token + position embeddings, MLM head')
def _bert():
    torch, nn, _ = _t()

    class M(nn.Module):
        def __init__(self, v=3000, d=128, n=32):
            super().__init__()
            self.tok, self.pos = nn.Embedding(v, d), nn.Embedding(n, d)
            layer = nn.TransformerEncoderLayer(d, 4, 4 * d, batch_first=True,
                                               activation='gelu')
            self.enc = nn.TransformerEncoder(layer, 2, enable_nested_tensor=False)
            self.norm, self.head = nn.LayerNorm(d), nn.Linear(d, v)
            self.register_buffer('ix', torch.arange(n))

        def forward(self, ids):
            h = self.tok(ids) + self.pos(self.ix[:ids.shape[1]])
            return self.head(self.norm(self.enc(h)))
    return M(), (torch.randint(0, 3000, (2, 32)),), ['input_ids'], ['logits']


@_torch_arch('gpt-tiny', 'text-generation', 'text',
             'decoder-only language model: causal self-attention, tied LM head')
def _gpt():
    torch, nn, F = _t()

    class Blk(nn.Module):
        def __init__(self, d, h):
            super().__init__()
            self.n1, self.n2 = nn.LayerNorm(d), nn.LayerNorm(d)
            self.qkv, self.o = nn.Linear(d, 3 * d), nn.Linear(d, d)
            self.ff = nn.Sequential(nn.Linear(d, 4 * d), nn.GELU(), nn.Linear(4 * d, d))
            self.h = h

        def forward(self, x, mask):
            b, t, d = x.shape
            q, k, v = self.qkv(self.n1(x)).split(d, 2)
            q, k, v = [z.reshape(b, t, self.h, d // self.h).transpose(1, 2) for z in (q, k, v)]
            a = (q @ k.transpose(-1, -2)) / (d // self.h) ** 0.5 + mask[:t, :t]
            y = (a.softmax(-1) @ v).transpose(1, 2).reshape(b, t, d)
            x = x + self.o(y)
            return x + self.ff(self.n2(x))

    class GPT(nn.Module):
        def __init__(self, v=4096, d=128, n=64):
            super().__init__()
            self.tok, self.pos = nn.Embedding(v, d), nn.Embedding(n, d)
            self.blocks = nn.ModuleList([Blk(d, 4) for _ in range(2)])
            self.norm = nn.LayerNorm(d)
            self.register_buffer('mask', torch.full((n, n), float('-inf')).triu(1))
            self.register_buffer('ix', torch.arange(n))

        def forward(self, ids):
            x = self.tok(ids) + self.pos(self.ix[:ids.shape[1]])
            for b in self.blocks:
                x = b(x, self.mask)
            return self.norm(x) @ self.tok.weight.T
    return GPT(), (torch.randint(0, 4096, (1, 64)),), ['input_ids'], ['logits']


@_torch_arch('llama-block', 'text-generation', 'text',
             'the modern decoder block: RMSNorm, SwiGLU, grouped-query attention')
def _llama():
    torch, nn, _ = _t()

    class RMS(nn.Module):
        def __init__(self, d):
            super().__init__()
            self.w = nn.Parameter(torch.ones(d))

        def forward(self, x):
            return x * torch.rsqrt(x.pow(2).mean(-1, keepdim=True) + 1e-6) * self.w

    class Blk(nn.Module):
        def __init__(self, d=128, h=8, kv=2, n=32):
            super().__init__()
            self.h, self.kv, self.hd = h, kv, d // h
            self.q = nn.Linear(d, d, bias=False)
            self.k = nn.Linear(d, kv * self.hd, bias=False)
            self.v = nn.Linear(d, kv * self.hd, bias=False)
            self.o = nn.Linear(d, d, bias=False)
            self.g, self.u = nn.Linear(d, 344, bias=False), nn.Linear(d, 344, bias=False)
            self.dn = nn.Linear(344, d, bias=False)
            self.n1, self.n2 = RMS(d), RMS(d)
            self.register_buffer('mask', torch.full((n, n), float('-inf')).triu(1))

        def forward(self, x):
            b, t, d = x.shape
            y = self.n1(x)
            q = self.q(y).reshape(b, t, self.h, self.hd).transpose(1, 2)
            k = self.k(y).reshape(b, t, self.kv, self.hd).transpose(1, 2) \
                .repeat_interleave(self.h // self.kv, 1)
            v = self.v(y).reshape(b, t, self.kv, self.hd).transpose(1, 2) \
                .repeat_interleave(self.h // self.kv, 1)
            a = (q @ k.transpose(-1, -2)) / self.hd ** 0.5 + self.mask[:t, :t]
            x = x + self.o((a.softmax(-1) @ v).transpose(1, 2).reshape(b, t, d))
            y = self.n2(x)
            return x + self.dn(torch.nn.functional.silu(self.g(y)) * self.u(y))
    return Blk(), (torch.randn(1, 32, 128),), ['hidden_states'], ['output']


@_torch_arch('moe', 'text-generation', 'text',
             'mixture of experts: a router scores 4 expert MLPs, top-2 are mixed')
def _moe():
    torch, nn, _ = _t()

    class MoE(nn.Module):
        def __init__(self, d=64, e=4, k=2):
            super().__init__()
            self.gate = nn.Linear(d, e)
            self.ex = nn.ModuleList([nn.Sequential(nn.Linear(d, 128), nn.GELU(),
                                                   nn.Linear(128, d)) for _ in range(e)])
            self.k = k

        def forward(self, x):
            w, i = self.gate(x).topk(self.k, -1)
            w = w.softmax(-1)
            outs = torch.stack([e(x) for e in self.ex], -2)       # b t e d
            pick = torch.gather(outs, -2, i.unsqueeze(-1).expand(*i.shape, x.shape[-1]))
            return (pick * w.unsqueeze(-1)).sum(-2)
    return MoE(), (torch.randn(2, 16, 64),), ['input'], ['output']


@_torch_arch('seq2seq', 'translation', 'text',
             'full encoder–decoder transformer (nn.Transformer): src and tgt in')
def _s2s():
    torch, nn, _ = _t()

    class M(nn.Module):
        def __init__(self):
            super().__init__()
            self.t = nn.Transformer(64, 4, 2, 2, 128, batch_first=True)
            self.h = nn.Linear(64, 500)

        def forward(self, src, tgt):
            return self.h(self.t(src, tgt))
    return M(), (torch.randn(1, 12, 64), torch.randn(1, 10, 64)), ['src', 'tgt'], ['logits']


@_torch_arch('kws-conv1d', 'audio-classification', 'audio',
             'keyword spotter: 1-D convs over 40 MFCC channels')
def _kws():
    torch, nn, _ = _t()
    m = nn.Sequential(nn.Conv1d(40, 64, 5, padding=2), nn.BatchNorm1d(64), nn.ReLU(),
                      nn.Conv1d(64, 64, 5, padding=2, groups=64), nn.Conv1d(64, 64, 1),
                      nn.BatchNorm1d(64), nn.ReLU(), nn.AdaptiveAvgPool1d(1),
                      nn.Flatten(), nn.Linear(64, 12))
    return m, (torch.randn(2, 40, 98),), ['mfcc'], ['logits']


@_torch_arch('tcn', 'audio-classification', 'audio',
             'temporal conv net: dilated causal 1-D convs, receptive field 64')
def _tcn():
    torch, nn, F = _t()

    class Res(nn.Module):
        def __init__(self, c, d):
            super().__init__()
            self.c, self.pad = nn.Conv1d(c, c, 3, dilation=d), 2 * d

        def forward(self, x):
            return x + F.relu(self.c(F.pad(x, (self.pad, 0))))

    m = nn.Sequential(nn.Conv1d(1, 32, 1), *[Res(32, 2 ** i) for i in range(5)],
                      nn.Conv1d(32, 1, 1))
    return m, (torch.randn(2, 1, 256),), ['waveform'], ['output']


@_torch_arch('conformer-lite', 'automatic-speech-recognition', 'audio',
             'speech encoder: conv subsampling, then attention + depthwise-conv module')
def _conformer():
    torch, nn, _ = _t()

    class ConvMod(nn.Module):
        def __init__(self, d):
            super().__init__()
            self.n = nn.LayerNorm(d)
            self.pw1, self.dw = nn.Conv1d(d, 2 * d, 1), nn.Conv1d(d, d, 15, padding=7, groups=d)
            self.bn, self.pw2 = nn.BatchNorm1d(d), nn.Conv1d(d, d, 1)

        def forward(self, x):
            y = self.n(x).transpose(1, 2)
            y = torch.nn.functional.glu(self.pw1(y), 1)
            y = self.pw2(torch.nn.functional.silu(self.bn(self.dw(y))))
            return x + y.transpose(1, 2)

    class M(nn.Module):
        def __init__(self, d=96):
            super().__init__()
            self.sub = nn.Sequential(nn.Conv2d(1, 16, 3, 2), nn.ReLU(), nn.Conv2d(16, 16, 3, 2),
                                     nn.ReLU())
            self.lin = nn.Linear(16 * 19, d)
            self.att = nn.TransformerEncoderLayer(d, 4, 2 * d, batch_first=True)
            self.conv = ConvMod(d)
            self.ctc = nn.Linear(d, 32)

        def forward(self, mel):
            x = self.sub(mel.unsqueeze(1))
            x = self.lin(x.permute(0, 2, 1, 3).flatten(2))
            return self.ctc(self.conv(self.att(x)))
    return M(), (torch.randn(1, 100, 80),), ['mel'], ['logits']


@_torch_arch('conv3d-video', 'video-classification', 'vision',
             '3-D convolutions over (frames, height, width)')
def _c3d():
    torch, nn, _ = _t()
    m = nn.Sequential(nn.Conv3d(3, 16, 3, padding=1), nn.BatchNorm3d(16), nn.ReLU(),
                      nn.MaxPool3d(2), nn.Conv3d(16, 32, 3, padding=1), nn.ReLU(),
                      nn.AdaptiveAvgPool3d(1), nn.Flatten(), nn.Linear(32, 10))
    return m, (torch.randn(1, 3, 8, 32, 32),), ['video'], ['logits']


@_torch_arch('pointnet', 'object-detection', 'vision',
             'point-cloud classifier: shared per-point MLP, symmetric max-pool')
def _pointnet():
    torch, nn, _ = _t()

    class M(nn.Module):
        def __init__(self):
            super().__init__()
            self.f = nn.Sequential(nn.Conv1d(3, 64, 1), nn.BatchNorm1d(64), nn.ReLU(),
                                   nn.Conv1d(64, 128, 1), nn.BatchNorm1d(128), nn.ReLU(),
                                   nn.Conv1d(128, 256, 1))
            self.h = nn.Sequential(nn.Linear(256, 128), nn.ReLU(), nn.Linear(128, 40))

        def forward(self, pts):
            return self.h(self.f(pts).amax(2))
    return M(), (torch.randn(2, 3, 512),), ['points'], ['logits']


@_torch_arch('siamese', 'sentence-similarity', 'vision',
             'two images through one shared backbone, cosine similarity out')
def _siamese():
    torch, nn, F = _t()

    class M(nn.Module):
        def __init__(self):
            super().__init__()
            self.b = nn.Sequential(nn.Conv2d(1, 16, 3, 2, 1), nn.ReLU(),
                                   nn.Conv2d(16, 32, 3, 2, 1), nn.ReLU(),
                                   nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Linear(32, 32))

        def forward(self, a, b):
            return F.cosine_similarity(self.b(a), self.b(b))
    return M(), (torch.randn(2, 1, 28, 28), torch.randn(2, 1, 28, 28)), ['left', 'right'], ['similarity']


@_torch_arch('gcn', 'graph-ml', 'graph',
             'graph convolution: node features and a dense adjacency matrix in')
def _gcn():
    torch, nn, _ = _t()

    class M(nn.Module):
        def __init__(self):
            super().__init__()
            self.l1, self.l2 = nn.Linear(16, 32), nn.Linear(32, 4)

        def forward(self, x, adj):
            deg = adj.sum(-1, keepdim=True).clamp(min=1)
            h = torch.relu(self.l1((adj @ x) / deg))
            return self.l2((adj @ h) / deg)
    return M(), (torch.randn(1, 50, 16), torch.rand(1, 50, 50).round()), ['nodes', 'adjacency'], ['output']


@_torch_arch('dlrm', 'tabular-classification', 'tabular',
             'recommender: dense features + sparse ids → embeddings, dot interaction')
def _dlrm():
    torch, nn, _ = _t()

    class M(nn.Module):
        def __init__(self, tables=4, rows=1000, d=16):
            super().__init__()
            self.bot = nn.Sequential(nn.Linear(13, 64), nn.ReLU(), nn.Linear(64, d))
            self.emb = nn.ModuleList([nn.Embedding(rows, d) for _ in range(tables)])
            n = tables + 1
            self.top = nn.Sequential(nn.Linear(n * (n - 1) // 2 + d, 64), nn.ReLU(),
                                     nn.Linear(64, 1), nn.Sigmoid())
            self.register_buffer('iu', torch.triu_indices(n, n, 1))

        def forward(self, dense, sparse):
            z = self.bot(dense)
            feats = torch.stack([z] + [e(sparse[:, i]) for i, e in enumerate(self.emb)], 1)
            inter = feats @ feats.transpose(1, 2)
            inter = inter[:, self.iu[0], self.iu[1]]
            return self.top(torch.cat([z, inter], 1))
    return M(), (torch.randn(4, 13), torch.randint(0, 1000, (4, 4))), ['dense', 'sparse'], ['ctr']


@_torch_arch('sentence-embedder', 'feature-extraction', 'text',
             'sentence embedder: mean over an encoder, then L2-normalised')
def _embedder():
    torch, nn, F = _t()

    class M(nn.Module):
        def __init__(self, v=3000, d=96):
            super().__init__()
            self.e = nn.Embedding(v, d)
            layer = nn.TransformerEncoderLayer(d, 4, 2 * d, batch_first=True)
            self.enc = nn.TransformerEncoder(layer, 1, enable_nested_tensor=False)

        def forward(self, ids, mask):
            h = self.enc(self.e(ids)) * mask.unsqueeze(-1)
            return F.normalize(h.sum(1) / mask.sum(1, keepdim=True).clamp(min=1), dim=-1)
    return M(), (torch.randint(0, 3000, (2, 24)), torch.ones(2, 24)), \
        ['input_ids', 'attention_mask'], ['embedding']


# ── onnx.helper builders: no torch needed ───────────────────────

def _h():
    import numpy as np
    from onnx import TensorProto, helper, numpy_helper
    return np, TensorProto, helper, numpy_helper


def _model(graph, opsets=(('', 17),)):
    _, _, helper, _ = _h()
    m = helper.make_model(graph, opset_imports=[helper.make_opsetid(d, v) for d, v in opsets],
                          producer_name='mod-infer')
    m.ir_version = 8
    return m


@_onnx_arch('linear-regression', 'tabular-regression', 'tabular',
            'y = xW + b, written straight in onnx.helper')
def _linreg():
    np, TP, helper, nh = _h()
    rng = np.random.default_rng(0)
    g = helper.make_graph(
        [helper.make_node('MatMul', ['x', 'W'], ['xw']), helper.make_node('Add', ['xw', 'b'], ['y'])],
        'linreg', [helper.make_tensor_value_info('x', TP.FLOAT, ['batch', 20])],
        [helper.make_tensor_value_info('y', TP.FLOAT, ['batch', 1])],
        [nh.from_array(rng.standard_normal((20, 1)).astype('float32'), 'W'),
         nh.from_array(np.zeros(1, 'float32'), 'b')])
    return _model(g)


@_onnx_arch('logistic-regression', 'tabular-classification', 'tabular',
            'Gemm then Sigmoid — the smallest classifier there is')
def _logreg():
    np, TP, helper, nh = _h()
    rng = np.random.default_rng(1)
    g = helper.make_graph(
        [helper.make_node('Gemm', ['x', 'W', 'b'], ['z']), helper.make_node('Sigmoid', ['z'], ['p'])],
        'logreg', [helper.make_tensor_value_info('x', TP.FLOAT, ['batch', 30])],
        [helper.make_tensor_value_info('p', TP.FLOAT, ['batch', 3])],
        [nh.from_array(rng.standard_normal((30, 3)).astype('float32'), 'W'),
         nh.from_array(np.zeros(3, 'float32'), 'b')])
    return _model(g)


@_onnx_arch('linear-classifier-ml', 'tabular-classification', 'tabular',
            'ai.onnx.ml LinearClassifier — what sklearn LogisticRegression exports to')
def _linclf():
    np, TP, helper, _ = _h()
    rng = np.random.default_rng(2)
    node = helper.make_node('LinearClassifier', ['x'], ['label', 'scores'], domain='ai.onnx.ml',
                            coefficients=rng.standard_normal(3 * 10).astype('float32').tolist(),
                            intercepts=[0.0, 0.1, -0.1], classlabels_ints=[0, 1, 2],
                            post_transform='SOFTMAX', multi_class=1)
    g = helper.make_graph([node], 'linclf',
                          [helper.make_tensor_value_info('x', TP.FLOAT, ['batch', 10])],
                          [helper.make_tensor_value_info('label', TP.INT64, ['batch']),
                           helper.make_tensor_value_info('scores', TP.FLOAT, ['batch', 3])])
    return _model(g, (('', 17), ('ai.onnx.ml', 3)))


@_onnx_arch('tree-ensemble', 'tabular-classification', 'tabular',
            'gradient-boosted forest: 20 depth-3 trees in ai.onnx.ml TreeEnsembleClassifier')
def _trees():
    np, TP, helper, _ = _h()
    rng = np.random.default_rng(3)
    F, T = 8, 20
    k = dict(nodes_treeids=[], nodes_nodeids=[], nodes_featureids=[], nodes_values=[],
             nodes_modes=[], nodes_truenodeids=[], nodes_falsenodeids=[],
             nodes_hitrates=[], nodes_missing_value_tracks_true=[],
             class_treeids=[], class_nodeids=[], class_ids=[], class_weights=[])
    for t in range(T):
        for n in range(15):                       # 7 branches + 8 leaves, heap order
            leaf = n >= 7
            k['nodes_treeids'].append(t)
            k['nodes_nodeids'].append(n)
            k['nodes_featureids'].append(0 if leaf else int(rng.integers(0, F)))
            k['nodes_values'].append(0.0 if leaf else float(rng.standard_normal()))
            k['nodes_modes'].append('LEAF' if leaf else 'BRANCH_LEQ')
            k['nodes_truenodeids'].append(0 if leaf else 2 * n + 1)
            k['nodes_falsenodeids'].append(0 if leaf else 2 * n + 2)
            k['nodes_hitrates'].append(1.0)
            k['nodes_missing_value_tracks_true'].append(0)
            if leaf:
                for c in (0, 1):
                    k['class_treeids'].append(t)
                    k['class_nodeids'].append(n)
                    k['class_ids'].append(c)
                    k['class_weights'].append(float(rng.standard_normal() * 0.1))
    node = helper.make_node('TreeEnsembleClassifier', ['x'], ['label', 'probabilities'],
                            domain='ai.onnx.ml', classlabels_int64s=[0, 1],
                            post_transform='LOGISTIC', **k)
    g = helper.make_graph([node], 'gbdt',
                          [helper.make_tensor_value_info('x', TP.FLOAT, ['batch', F])],
                          [helper.make_tensor_value_info('label', TP.INT64, ['batch']),
                           helper.make_tensor_value_info('probabilities', TP.FLOAT, ['batch', 2])])
    return _model(g, (('', 17), ('ai.onnx.ml', 3)))


@_onnx_arch('kmeans', 'tabular-classification', 'tabular',
            'nearest-centroid assignment: squared distances, ArgMin')
def _kmeans():
    np, TP, helper, nh = _h()
    rng = np.random.default_rng(4)
    C = rng.standard_normal((8, 16)).astype('float32')
    nodes = [
        helper.make_node('Unsqueeze', ['x', 'ax1'], ['xe']),
        helper.make_node('Sub', ['xe', 'C'], ['d']),
        helper.make_node('Mul', ['d', 'd'], ['d2']),
        helper.make_node('ReduceSum', ['d2', 'ax2'], ['dist'], keepdims=0),
        helper.make_node('ArgMin', ['dist'], ['cluster'], axis=1, keepdims=0),
    ]
    g = helper.make_graph(nodes, 'kmeans',
                          [helper.make_tensor_value_info('x', TP.FLOAT, ['batch', 16])],
                          [helper.make_tensor_value_info('cluster', TP.INT64, ['batch']),
                           helper.make_tensor_value_info('dist', TP.FLOAT, ['batch', 8])],
                          [nh.from_array(C, 'C'), nh.from_array(np.array([1], 'int64'), 'ax1'),
                           nh.from_array(np.array([2], 'int64'), 'ax2')])
    return _model(g)


@_onnx_arch('control-flow', 'other', 'other',
            'If and Loop subgraphs — the graph walks into branches and iterations')
def _control():
    np, TP, helper, nh = _h()
    then_g = helper.make_graph([helper.make_node('Relu', ['x'], ['t'])], 'then', [],
                               [helper.make_tensor_value_info('t', TP.FLOAT, None)])
    else_g = helper.make_graph([helper.make_node('Neg', ['x'], ['e'])], 'else', [],
                               [helper.make_tensor_value_info('e', TP.FLOAT, None)])
    body = helper.make_graph(
        [helper.make_node('Identity', ['cond_in'], ['cond_out']),
         helper.make_node('Mul', ['acc_in', 'half'], ['scaled']),
         helper.make_node('Add', ['scaled', 'x'], ['acc_out'])],
        'body',
        [helper.make_tensor_value_info('iter', TP.INT64, []),
         helper.make_tensor_value_info('cond_in', TP.BOOL, []),
         helper.make_tensor_value_info('acc_in', TP.FLOAT, None)],
        [helper.make_tensor_value_info('cond_out', TP.BOOL, []),
         helper.make_tensor_value_info('acc_out', TP.FLOAT, None)])
    nodes = [
        helper.make_node('ReduceMean', ['x'], ['m'], keepdims=0),
        helper.make_node('Greater', ['m', 'zero'], ['pos']),
        helper.make_node('If', ['pos'], ['branch'], then_branch=then_g, else_branch=else_g),
        helper.make_node('Loop', ['trips', 'true', 'branch'], ['y'], body=body),
    ]
    g = helper.make_graph(nodes, 'control',
                          [helper.make_tensor_value_info('x', TP.FLOAT, ['batch', 32])],
                          [helper.make_tensor_value_info('y', TP.FLOAT, ['batch', 32])],
                          [nh.from_array(np.array(0, 'float32'), 'zero'),
                           nh.from_array(np.array(0.5, 'float32'), 'half'),
                           nh.from_array(np.array(4, 'int64'), 'trips'),
                           nh.from_array(np.array(True), 'true')])
    return _model(g)


# ── the source ───────────────────────────────────────────────────

def build(name):
    """→ bytes of one architecture, built fresh and deterministically."""
    if name not in ARCHS:
        raise ZooError(f'no builtin architecture {name!r} — have: {", ".join(ARCHS)}', 404)
    how, fn = ARCHS[name][:2]
    if how == 'onnx':
        return fn().SerializeToString()
    try:
        import torch
    except ImportError:
        raise ZooError(f'{name} is built with torch, which is not installed — the '
                       f'onnx.helper ones still work', 501)
    torch.manual_seed(0)
    return export_torch(*fn())


def export_torch(module, inputs, names_in, names_out, opset=17):
    """nn.Module + example inputs → ONNX bytes, batch axis dynamic."""
    import torch
    module = module.eval()
    dyn = {n: {0: 'batch'} for n in names_in + names_out}
    tmp = tempfile.mkdtemp(prefix='infer-build-')
    dst = os.path.join(tmp, 'model.onnx')
    try:
        # Not under no_grad: with gradients off, nn.MultiheadAttention and
        # nn.TransformerEncoderLayer take a fused fast path
        # (_native_multi_head_attention) that has no ONNX export.
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            torch.onnx.export(module, inputs, dst, input_names=names_in,
                              output_names=names_out, dynamic_axes=dyn,
                              opset_version=opset, dynamo=False)
        with open(dst, 'rb') as f:
            return f.read()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


class Builtin(Source):
    name = 'builtin'
    title = 'every architecture, built here'
    kind = 'build'
    home = ''
    note = ('one small model per architecture family, constructed on this box — '
            'torch for the networks, onnx.helper for classical ML and control flow')
    expect = len(ARCHS)

    def scrape(self, job, state):
        rows = [entry(self.name, n, name=n, files=[['model.onnx', None]], task=task,
                      domain=domain, about=what, author='infer', builder=how,
                      tags=[how, task], local=True, license='mit')
                for n, (how, _, task, domain, what) in ARCHS.items()]
        yield rows, {}, True, len(rows)

    def fetch(self, e, path, job=None, max_bytes=None):
        return build(e['ref'])
