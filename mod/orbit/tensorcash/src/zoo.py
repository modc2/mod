"""The zoo: models small enough to read, real enough to mean something.

Three ship, and they are chosen to cover the three honest cases:

xor        Weights set BY HAND from the textbook construction, so the
           model is correct by inspection and the test suite can assert
           exact outputs. Four inputs, four known answers: the place to
           look first when the engine seems wrong.

digits     An 8x8 digit classifier (64-24-10) actually trained, by
           tools/train_digits.py, with float SGD on generated glyph
           variants and then quantised to int8. 86.8% on a held-out set,
           float and quantised alike. This is the one that makes the point:
           the thing being proven is a model that does a job.

sentiment  Bag-of-words over a 32-word vocabulary through the `embed` op,
           so the trace covers token input and mean pooling rather than
           only dense layers. Weights are hand-set, polarity per word.

A zoo model is a tcm1 blob like any other — nothing in the runtime knows
these three from a model someone uploads.
"""

from __future__ import annotations

import os

import model as M
import tensors as T

HERE = os.path.dirname(os.path.abspath(__file__))
ZOO_DIR = os.path.join(HERE, 'zoo')

VOCAB = ('<pad>', 'good', 'great', 'love', 'excellent', 'wonderful', 'best',
         'happy', 'fast', 'clean', 'works', 'bad', 'terrible', 'hate',
         'awful', 'worst', 'slow', 'broken', 'sad', 'useless', 'crash',
         'the', 'a', 'is', 'it', 'this', 'very', 'and', 'not', 'i', 'was',
         'really')
POSITIVE = set(range(1, 11))
NEGATIVE = set(range(11, 21))


def _xor_blob() -> bytes:
    """y = relu(s - 0.5) - 3*relu(s - 1.5), the standard two-hidden-unit
    XOR net, written out at a scale of 100 so int8 holds it exactly.

        s = x1 + x2            x in {0, 100}
        a = [s - 50, s - 150]  relu, halved into int8 range
        y = h1 - 3*h2          0 for (0,0) and (1,1), 25 for either alone
        logits = [12, y]       argmax: class 1 is "exactly one"
    """
    tensors = [
        {'name': 'fc1.w', 'dtype': 'i8', 'shape': [2, 2],
         'data': T.to_i8([1, 1, 1, 1])},
        {'name': 'fc1.b', 'dtype': 'i32', 'shape': [2],
         'data': T.to_i32([-50, -150])},
        {'name': 'fc2.w', 'dtype': 'i8', 'shape': [2, 2],
         'data': T.to_i8([0, 0, 1, -3])},
        {'name': 'fc2.b', 'dtype': 'i32', 'shape': [2],
         'data': T.to_i32([12, 0])},
    ]
    graph = [
        {'op': 'matvec', 'w': 'fc1.w', 'x': 'in', 'out': 'a1'},
        {'op': 'bias', 'b': 'fc1.b', 'x': 'a1', 'out': 'a1b'},
        {'op': 'relu', 'x': 'a1b', 'out': 'a1r'},
        {'op': 'requant', 'x': 'a1r', 'out': 'h1', 'mult': 1, 'shift': 1},
        {'op': 'matvec', 'w': 'fc2.w', 'x': 'h1', 'out': 'a2'},
        {'op': 'bias', 'b': 'fc2.b', 'x': 'a2', 'out': 'logits'},
        {'op': 'argmax', 'x': 'logits', 'out': 'cls'},
    ]
    io = {'input': {'buffer': 'in', 'dtype': 'i8', 'len': 2,
                    'what': 'two bits as 0 or 100'},
          'output': {'buffer': 'logits', 'dtype': 'i32'},
          'class': 'cls', 'labels': ['same', 'different']}
    return M.build('xor', tensors, graph, io, meta={
        'what': 'exclusive or, by hand — correct by inspection',
        'try': [[0, 0], [100, 0], [0, 100], [100, 100]],
        'params': 12})


def _sentiment_blob() -> bytes:
    dim = 2
    rows = []
    for i in range(len(VOCAB)):
        if i in POSITIVE:
            rows += [100, -100]
        elif i in NEGATIVE:
            rows += [-100, 100]
        else:
            rows += [0, 0]
    tensors = [
        {'name': 'emb', 'dtype': 'i8', 'shape': [len(VOCAB), dim],
         'data': T.to_i8(rows)},
        {'name': 'head.w', 'dtype': 'i8', 'shape': [2, dim],
         'data': T.to_i8([1, 0, 0, 1])},
        {'name': 'head.b', 'dtype': 'i32', 'shape': [2],
         'data': T.to_i32([1, 0])},
    ]
    graph = [
        {'op': 'embed', 'table': 'emb', 'ids': 'in', 'out': 'e',
         'pool': 'mean'},
        {'op': 'requant', 'x': 'e', 'out': 'eq', 'mult': 1, 'shift': 0},
        {'op': 'matvec', 'w': 'head.w', 'x': 'eq', 'out': 'logits0'},
        {'op': 'bias', 'b': 'head.b', 'x': 'logits0', 'out': 'logits'},
        {'op': 'argmax', 'x': 'logits', 'out': 'cls'},
    ]
    io = {'input': {'buffer': 'in', 'dtype': 'idx',
                    'what': 'token ids into the 32-word vocabulary'},
          'output': {'buffer': 'logits', 'dtype': 'i32'},
          'class': 'cls', 'labels': ['positive', 'negative']}
    return M.build('sentiment', tensors, graph, io, meta={
        'what': 'bag-of-words polarity over a 32-word vocabulary',
        'vocab': list(VOCAB),
        'tie': 'the +1 bias on lane 0 breaks an exact tie towards positive',
        'params': len(VOCAB) * dim + 6})


BUILDERS = {'xor': _xor_blob, 'sentiment': _sentiment_blob}
FILES = {'digits': 'digits.tcm1'}


def names() -> list[str]:
    return sorted(set(BUILDERS) | set(FILES))


def blob(name: str) -> bytes:
    if name in BUILDERS:
        return BUILDERS[name]()
    if name in FILES:
        path = os.path.join(ZOO_DIR, FILES[name])
        try:
            with open(path, 'rb') as f:
                return f.read()
        except OSError:
            raise M.ModelError(
                f'the {name} blob is missing — rebuild it with '
                'python3 tools/train_digits.py', 'no_model', 404) from None
    raise M.ModelError(f'no model {name!r} in the zoo — '
                       f'{", ".join(names())}', 'no_model', 404)


def catalog() -> list[dict]:
    out = []
    for name in names():
        try:
            m = M.parse(blob(name))
        except M.ModelError as e:
            out.append({'name': name, 'missing': True, 'reason': str(e)})
            continue
        out.append({'name': name, 'cid': m['cid'],
                    'model_root': m['model_root'], 'bytes': m['bytes'],
                    'steps': len(m['graph']),
                    'tensors': [t['name'] for t in m['table']],
                    'params': (m['meta'] or {}).get('params'),
                    'what': (m['meta'] or {}).get('what'),
                    'io': m['io'], 'meta': m['meta'],
                    'hand_built': name in BUILDERS})
    return out


def tokenize(text: str) -> list[int]:
    """Words the sentiment model knows; unknown words are dropped (they
    would otherwise all map to one id and vote)."""
    ids = []
    for w in str(text).lower().replace(',', ' ').replace('.', ' ').split():
        if w in VOCAB:
            ids.append(VOCAB.index(w))
    return ids
