"""The models. Built here, on this box, from numpy — not downloaded.

Two of them, both small enough to open in a text editor's hex view and both
written as real ONNX files by `onnxfile.py`:

`bow-64`   an embedder. 8192 hash buckets × 64 dimensions of random projection,
           mean-pooled over the words of a sentence and normalised. 2.0 MB.
`sent-mlp` a sentiment classifier, one hidden layer, trained here on the
           synthetic rows in `data.py`. 1.0 MB.

Neither is good at its job in the way a downloaded transformer is good at its
job, and neither is pretending to be. They exist because compression is much
easier to understand on a model you watched get built: when int8 moves an
answer you can go back to the weights and see the row it happened in.

`pull()` fetches a real ONNX model from Hugging Face for when you want the
compressor pointed at something with a hundred times the parameters. That is the
only function here that touches the network.

The random projection in `bow-64` is not a placeholder for real training. A
matrix of gaussian noise mapping 8192 dimensions down to 64 approximately keeps
the angles between the vectors it maps (Johnson–Lindenstrauss), so cosine
similarity over these 64 numbers is a decent stand-in for word overlap between
the sentences — which is a genuine, if shallow, notion of similarity.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from . import data, onnxfile, text
from .onnxfile import Attribute, Graph, Model, Node, Tensor, ValueInfo

HOME = Path(os.environ.get('EMBED_DIR', Path.home() / '.mod' / 'embed'))
MODELS = HOME / 'models'

BOW_VOCAB, BOW_DIM = 8192, 64
MLP_VOCAB, MLP_HIDDEN = 4096, 64

CATALOG: Dict[str, Dict[str, Any]] = {
    'bow-64': {
        'kind': 'embedder',
        'built': 'here, from a seed — no training, no download',
        'about': 'hashing bag-of-words → 64-d random projection, mean-pooled, normalised',
        'input': 'input_ids: int64[tokens]',
        'output': 'vector: float32[64]',
        'ops': ['Gather', 'ReduceMean', 'ReduceL2', 'Add', 'Div'],
    },
    'sent-mlp': {
        'kind': 'classifier',
        'built': 'trained here in numpy on src/data.py, ~2 seconds',
        'about': '4096-bucket bag → 64 hidden units → 2 classes',
        'input': 'features: float32[batch, 4096]',
        'output': 'probabilities: float32[batch, 2]',
        'ops': ['MatMul', 'Add', 'Relu', 'Softmax'],
    },
    'minilm': {
        'kind': 'embedder',
        'built': 'downloaded — sentence-transformers/all-MiniLM-L6-v2, onnx/model.onnx',
        'about': 'a real 22M-parameter transformer, ~90 MB, for compressing at scale',
        'input': 'input_ids / attention_mask / token_type_ids: int64[batch, seq]',
        'output': 'last_hidden_state: float32[batch, seq, 384]',
        'ops': ['the full transformer set — more than runtime.py implements'],
        'network': True,
    },
}


def path(name: str) -> Path:
    return MODELS / f'{name}.onnx'


def catalog() -> List[Dict[str, Any]]:
    rows = []
    for name, info in CATALOG.items():
        file = path(name)
        rows.append({'name': name, **info, 'present': file.exists(),
                     'bytes': file.stat().st_size if file.exists() else None,
                     'path': str(file)})
    return rows


def ensure(name: str) -> Path:
    """The model, built if this is the first time anyone asked for it."""
    file = path(name)
    if file.exists():
        return file
    if name == 'bow-64':
        return build_bow()
    if name == 'sent-mlp':
        return build_mlp()
    if name == 'minilm':
        raise FileNotFoundError('minilm has to be downloaded: m embed/pull name=minilm')
    raise KeyError(f'unknown model {name!r} — {sorted(CATALOG)}')


def load(name: str) -> Model:
    return onnxfile.load(ensure(name))


# ── bow-64 ───────────────────────────────────────────────────────────

def build_bow(vocab: int = BOW_VOCAB, dim: int = BOW_DIM, seed: int = 1) -> Path:
    """Write the embedder out as ONNX. Deterministic: same seed, same bytes."""
    rng = np.random.default_rng(seed)
    matrix = rng.normal(0.0, 1.0 / np.sqrt(dim), size=(vocab, dim)).astype(np.float32)

    graph = Graph(
        name='bow-64',
        initializers=[Tensor('embedding', matrix),
                      Tensor('eps', np.array([1e-9], dtype=np.float32))],
        inputs=[ValueInfo('input_ids', dtype=7, shape=['tokens'])],
        outputs=[ValueInfo('vector', dtype=1, shape=[dim])],
        nodes=[
            Node('Gather', ['embedding', 'input_ids'], ['rows'],
                 attributes=[Attribute.i('axis', 0)]),
            Node('ReduceMean', ['rows'], ['pooled'],
                 attributes=[Attribute.ints('axes', [0]), Attribute.i('keepdims', 0)]),
            Node('ReduceL2', ['pooled'], ['length'],
                 attributes=[Attribute.ints('axes', [0]), Attribute.i('keepdims', 1)]),
            Node('Add', ['length', 'eps'], ['safe_length']),
            Node('Div', ['pooled', 'safe_length'], ['vector']),
        ],
    )
    MODELS.mkdir(parents=True, exist_ok=True)
    written = onnxfile.save(Model(graph), path('bow-64'))
    _record('bow-64', {'vocab': vocab, 'dim': dim, 'seed': seed, 'bytes': written})
    return path('bow-64')


# ── sent-mlp ─────────────────────────────────────────────────────────

def build_mlp(vocab: int = MLP_VOCAB, hidden: int = MLP_HIDDEN, seed: int = 3,
              epochs: int = 200, lr: float = 1.0, init: float = 0.5) -> Path:
    """Train the classifier here, then write it out. No framework involved.

    `init` is load-bearing and not a knob to turn idly: at 0.5 this trains to
    ~0.94 on the training rows and ~0.88 held out, and at 0.3 the first layer's
    outputs start below the ReLU's knee, most units never fire, and the same
    loop converges to exactly nothing (0.50 — the class prior). Two hundred
    epochs of full-batch gradient descent take about a second on 1,260 rows.
    """
    rows = data.sentiment()
    train, test = rows['train'], rows['test']
    x = np.stack([text.bag(t, vocab) for t, _ in train])
    y = np.array([label for _, label in train], dtype=np.int64)
    xt = np.stack([text.bag(t, vocab) for t, _ in test])
    yt = np.array([label for _, label in test], dtype=np.int64)

    rng = np.random.default_rng(seed)
    w1 = rng.normal(0, init, (vocab, hidden)).astype(np.float32)
    b1 = np.zeros(hidden, dtype=np.float32)
    w2 = rng.normal(0, 1 / np.sqrt(hidden), (hidden, 2)).astype(np.float32)
    b2 = np.zeros(2, dtype=np.float32)
    onehot = np.eye(2, dtype=np.float32)[y]

    for epoch in range(epochs):                       # full-batch, it is 1.2k rows
        h = np.maximum(x @ w1 + b1, 0)
        logits = h @ w2 + b2
        probs = _softmax(logits)
        delta = (probs - onehot) / len(x)
        gw2, gb2 = h.T @ delta, delta.sum(0)
        dh = (delta @ w2.T) * (h > 0)
        gw1, gb1 = x.T @ dh, dh.sum(0)
        for param, grad in ((w1, gw1), (b1, gb1), (w2, gw2), (b2, gb2)):
            param -= lr * grad.astype(np.float32)

    accuracy = float((np.argmax(_softmax(np.maximum(xt @ w1 + b1, 0) @ w2 + b2), 1)
                      == yt).mean())

    graph = Graph(
        name='sent-mlp',
        initializers=[Tensor('w1', w1), Tensor('b1', b1),
                      Tensor('w2', w2), Tensor('b2', b2)],
        inputs=[ValueInfo('features', dtype=1, shape=['batch', vocab])],
        outputs=[ValueInfo('probabilities', dtype=1, shape=['batch', 2])],
        nodes=[
            Node('MatMul', ['features', 'w1'], ['hidden_raw']),
            Node('Add', ['hidden_raw', 'b1'], ['hidden_biased']),
            Node('Relu', ['hidden_biased'], ['hidden']),
            Node('MatMul', ['hidden', 'w2'], ['logits_raw']),
            Node('Add', ['logits_raw', 'b2'], ['logits']),
            Node('Softmax', ['logits'], ['probabilities'],
                 attributes=[Attribute.i('axis', -1)]),
        ],
    )
    MODELS.mkdir(parents=True, exist_ok=True)
    written = onnxfile.save(Model(graph), path('sent-mlp'))
    _record('sent-mlp', {'vocab': vocab, 'hidden': hidden, 'seed': seed,
                         'epochs': epochs, 'lr': lr, 'init': init,
                         'test_accuracy': round(accuracy, 4), 'bytes': written})
    return path('sent-mlp')


def _softmax(z: np.ndarray) -> np.ndarray:
    e = np.exp(z - z.max(axis=-1, keepdims=True))
    return e / e.sum(axis=-1, keepdims=True)


# ── the one that downloads ───────────────────────────────────────────

def pull(name: str = 'minilm', repo: Optional[str] = None,
         file: Optional[str] = None) -> Dict[str, Any]:
    """Fetch a real ONNX model. The only networked function in the module."""
    import urllib.request
    repo = repo or 'sentence-transformers/all-MiniLM-L6-v2'
    file = file or 'onnx/model.onnx'
    url = f'https://huggingface.co/{repo}/resolve/main/{file}'
    target = path(name)
    MODELS.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with urllib.request.urlopen(url, timeout=300) as response, \
            open(target, 'wb') as out:
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    size = target.stat().st_size
    _record(name, {'repo': repo, 'file': file, 'bytes': size})
    return {'name': name, 'repo': repo, 'file': file, 'path': str(target),
            'bytes': size, 'seconds': round(time.time() - started, 1)}


# ── bookkeeping ──────────────────────────────────────────────────────

def _record(name: str, fields: Dict[str, Any]) -> None:
    """What each model was built from, kept next to the model."""
    ledger = MODELS / 'built.json'
    entries = json.loads(ledger.read_text()) if ledger.exists() else {}
    entries[name] = {**fields, 'built_at': int(time.time())}
    ledger.write_text(json.dumps(entries, indent=2))


def built() -> Dict[str, Any]:
    ledger = MODELS / 'built.json'
    return json.loads(ledger.read_text()) if ledger.exists() else {}
