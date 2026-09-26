"""What the compression cost, measured on the task rather than on the tensors.

Tensor error tells you the weights moved. It does not tell you whether any
answer changed, and those are different questions: a model can absorb a 1%
weight perturbation with no visible effect, or lose a search result to it,
depending on how close the contest was.

So every number here comes from running the model:

`retrieval`  ask the twelve questions in `data.py`, count how often the
             top-ranked sentence is from the right topic, and — the stricter
             measure — how often the compressed model returns the *same*
             ranking as the float one.
`sentiment`  accuracy on the held-out rows, and again the agreement rate.

Agreement is the number to watch. Accuracy can hold steady while a fifth of the
answers change underneath it, two mistakes cancelling one correction, and a
report that only prints accuracy will call that lossless.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import data, onnxfile, runtime, text, zoo
from .onnxfile import Model


# ── running the two models ───────────────────────────────────────────

def embed(model: Model, sentence: str, vocab: int = zoo.BOW_VOCAB,
          drop_stopwords: bool = True) -> np.ndarray:
    """Words → ids → the model. Stopwords are dropped before the model sees them.

    Mean pooling weights every token equally, so `the` and `a` pull every
    sentence toward the same average. Dropping them takes top-1 retrieval on the
    built-in questions from 0.75 to 0.83 without touching a weight — a reminder
    that at this size the tokenizer is doing as much work as the model.
    """
    ids = text.token_ids(sentence, vocab, drop_stopwords)
    return runtime.outputs(model, {'input_ids': ids})


def embed_all(model: Model, sentences: List[str]) -> np.ndarray:
    return np.stack([embed(model, s) for s in sentences])


def classify(model: Model, sentences: List[str],
             vocab: int = zoo.MLP_VOCAB) -> np.ndarray:
    features = np.stack([text.bag(s, vocab) for s in sentences])
    return runtime.outputs(model, {'features': features})


def search(model: Model, query: str, docs: Optional[List[str]] = None,
           top: int = 5) -> List[Dict[str, Any]]:
    """Rank sentences by cosine against the query. This is the whole of search."""
    corpus = docs if docs is not None else [d for _, d in data.DOCS]
    matrix = embed_all(model, corpus)
    vector = embed(model, query)
    scores = matrix @ vector / (
        np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector) + 1e-12)
    order = np.argsort(-scores)[:top]
    return [{'rank': i + 1, 'score': round(float(scores[j]), 4), 'text': corpus[j],
             'index': int(j)} for i, j in enumerate(order)]


# ── the two task metrics ─────────────────────────────────────────────

def retrieval(model: Model, reference: Optional[Model] = None) -> Dict[str, Any]:
    """Top-1 topic accuracy over the built-in questions, plus agreement."""
    topics = [t for t, _ in data.DOCS]
    corpus = [d for _, d in data.DOCS]
    matrix = embed_all(model, corpus)
    matrix = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12)
    reference_top: List[int] = []
    if reference is not None:
        ref_matrix = embed_all(reference, corpus)
        ref_matrix = ref_matrix / (np.linalg.norm(ref_matrix, axis=1, keepdims=True) + 1e-12)

    hits, agree, misses = 0, 0, []
    for question, expected in data.QUERIES:
        vector = embed(model, question)
        best = int(np.argmax(matrix @ vector))
        correct = topics[best] == expected
        hits += correct
        if not correct:
            misses.append({'query': question, 'expected': expected,
                           'got': topics[best], 'text': corpus[best]})
        if reference is not None:
            ref_vector = embed(reference, question)
            ref_best = int(np.argmax(ref_matrix @ ref_vector))
            reference_top.append(ref_best)
            agree += (best == ref_best)
    out: Dict[str, Any] = {
        'task': 'retrieval', 'queries': len(data.QUERIES),
        'top1_accuracy': round(hits / len(data.QUERIES), 4), 'misses': misses,
    }
    if reference is not None:
        out['agreement_with_float'] = round(agree / len(data.QUERIES), 4)
    return out


def sentiment(model: Model, reference: Optional[Model] = None) -> Dict[str, Any]:
    """Held-out accuracy, agreement, and how many answers were close calls."""
    rows = data.sentiment()['test']
    sentences = [t for t, _ in rows]
    labels = np.array([label for _, label in rows])
    probs = classify(model, sentences)
    predictions = probs.argmax(1)
    margin = np.abs(probs[:, 1] - probs[:, 0])
    out: Dict[str, Any] = {
        'task': 'sentiment', 'rows': len(rows),
        'accuracy': round(float((predictions == labels).mean()), 4),
        'close_calls': int((margin < 0.1).sum()),
        'mean_margin': round(float(margin.mean()), 4),
    }
    if reference is not None:
        ref = classify(reference, sentences).argmax(1)
        out['agreement_with_float'] = round(float((predictions == ref).mean()), 4)
        out['flipped'] = int((predictions != ref).sum())
    return out


def measure(model: Model, name: str, reference: Optional[Model] = None) -> Dict[str, Any]:
    """Whichever metric belongs to this model."""
    kind = zoo.CATALOG.get(name, {}).get('kind')
    if kind == 'classifier':
        return sentiment(model, reference)
    if kind == 'embedder':
        return retrieval(model, reference)
    return {'task': None, 'note': f'no task metric wired up for {name!r}'}


# ── the headline call ────────────────────────────────────────────────

def sweep(name: str = 'bow-64', methods: Optional[List[str]] = None,
          keep: bool = False) -> Dict[str, Any]:
    """Compress a model every way this module knows, and score each one.

    One row per method: what the file weighs, what the weights moved by, and
    what the task did. This is the table the README is arguing for.
    """
    from . import compress as compressor

    source = zoo.ensure(name)
    float_model = onnxfile.load(source)
    baseline = measure(float_model, name)
    rows = []
    for method in (methods or list(compressor.METHODS)):
        target = source.with_name(f'{source.stem}.{method}.onnx')
        report = compressor.compress_file(source, target, method)
        smaller = onnxfile.load(target)
        scored = measure(smaller, name, reference=float_model)
        worst = max((t.get('error', {}).get('rel_rmse', 0.0) for t in report['tensors']),
                    default=0.0)
        rows.append({
            'method': method,
            'file_bytes': report['file_bytes_after'],
            'file_ratio': report['file_ratio'],
            'gzip_bytes': report['gzip_bytes_after'],
            'worst_tensor_rel_rmse': round(worst, 5),
            **{k: v for k, v in scored.items() if k != 'misses'},
            'path': str(target),
        })
        if not keep:
            target.unlink(missing_ok=True)
    return {
        'model': name,
        'source': str(source),
        'source_bytes': source.stat().st_size,
        'float_baseline': baseline,
        'results': rows,
        'note': 'agreement_with_float is the strict metric; accuracy hides swaps',
    }
