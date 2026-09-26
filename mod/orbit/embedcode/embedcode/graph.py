"""
graph - turn a matrix of file embeddings into a 2D map with clusters.

Pure numpy/sklearn. No state, no I/O: give it vectors + metadata, get back
a dict of {nodes, edges, clusters} ready to draw.
"""
import os
import re
import math
from collections import Counter
from typing import List, Dict, Any

STOP = {
    'the', 'and', 'for', 'with', 'this', 'that', 'from', 'not', 'are', 'was',
    'you', 'your', 'all', 'any', 'can', 'has', 'have', 'will', 'new', 'get',
    'set', 'def', 'let', 'const', 'var', 'return', 'import', 'export', 'from',
    'class', 'self', 'true', 'false', 'null', 'none', 'if', 'else', 'try',
    'except', 'print', 'str', 'int', 'dict', 'list', 'type', 'name', 'value',
    'data', 'path', 'file', 'index', 'main', 'src', 'app', 'test', 'tests',
    'function', 'async', 'await', 'div', 'className', 'string', 'number',
    'default', 'props', 'state', 'use', 'react', 'next', 'lib', 'utils',
    'mod', 'module', 'modules', 'code', 'text', 'json', 'http', 'https',
}

TOKEN_RE = re.compile(r'[A-Za-z][A-Za-z0-9_]{2,}')


def _tokens(s: str) -> List[str]:
    out = []
    for t in TOKEN_RE.findall(s):
        # split camelCase / snake_case into words
        for part in re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', t).replace('_', ' ').split():
            p = part.lower()
            if len(p) > 2 and p not in STOP and not p.isdigit():
                out.append(p)
    return out


# ── layout ────────────────────────────────────────────────────────

def layout(vectors, method: str = 'tsne', seed: int = 0):
    """Project (n, dim) vectors down to (n, 2), normalized into [-1, 1]."""
    import numpy as np
    from sklearn.decomposition import PCA

    n = len(vectors)
    if n == 0:
        return np.zeros((0, 2), dtype=np.float32)
    if n == 1:
        return np.zeros((1, 2), dtype=np.float32)

    X = np.asarray(vectors, dtype=np.float32)

    # PCA first: denoise 384 -> 50 dims, and it's the init for t-SNE
    n_pca = int(min(50, n - 1, X.shape[1]))
    pcs = PCA(n_components=n_pca, random_state=seed).fit_transform(X)

    if method == 'pca' or n < 12:
        xy = pcs[:, :2]
    else:
        from sklearn.manifold import TSNE
        perplexity = float(max(5, min(40, (n - 1) / 3)))
        xy = TSNE(
            n_components=2,
            perplexity=perplexity,
            init=pcs[:, :2] / (np.std(pcs[:, 0]) or 1) * 1e-4,
            learning_rate='auto',
            metric='cosine',
            random_state=seed,
            max_iter=750,
        ).fit_transform(pcs)

    xy = np.asarray(xy, dtype=np.float32)
    # normalize into [-1, 1] preserving aspect ratio
    center = (xy.max(axis=0) + xy.min(axis=0)) / 2.0
    xy = xy - center
    scale = float(np.abs(xy).max()) or 1.0
    return xy / scale


# ── clustering ────────────────────────────────────────────────────

def cluster(vectors, k: int = 0, seed: int = 0):
    """KMeans over the embedding vectors (not the 2D layout). Returns labels."""
    import numpy as np
    from sklearn.cluster import KMeans

    n = len(vectors)
    if n == 0:
        return np.zeros(0, dtype=int), 0
    if k <= 0:
        k = int(max(6, min(28, round(math.sqrt(n / 2.0)))))
    k = int(min(k, n))
    km = KMeans(n_clusters=k, n_init=6, random_state=seed).fit(np.asarray(vectors, dtype=np.float32))
    return km.labels_, k


def label_clusters(labels, metadata: List[dict], k: int) -> List[dict]:
    """Name each cluster from the distinctive words in its files."""
    import numpy as np

    docs = []
    for meta in metadata:
        rel = meta.get('rel') or meta.get('path', '')
        # path words weigh 3x — file/dir names are the best summary we have
        words = _tokens(rel.replace('/', ' ')) * 3 + _tokens(meta.get('preview', '')[:900])
        docs.append(words)

    global_df = Counter()
    for d in docs:
        global_df.update(set(d))
    n_docs = max(1, len(docs))

    out = []
    for c in range(k):
        idx = [i for i, l in enumerate(labels) if int(l) == c]
        if not idx:
            out.append({'id': c, 'label': f'cluster {c}', 'terms': [], 'size': 0,
                        'modules': [], 'exts': []})
            continue
        tf = Counter()
        for i in idx:
            tf.update(set(docs[i]))
        scored = []
        for term, count in tf.items():
            if count < 2 and len(idx) > 3:
                continue
            share = count / len(idx)
            idf = math.log(n_docs / (1 + global_df[term]))
            scored.append((share * idf, term))
        scored.sort(reverse=True)
        terms = [t for _, t in scored[:6]]

        mods = Counter(metadata[i].get('module', '?') for i in idx)
        exts = Counter(metadata[i].get('ext', '') for i in idx)
        out.append({
            'id': c,
            'label': ' · '.join(terms[:3]) if terms else f'cluster {c}',
            'terms': terms,
            'size': len(idx),
            'modules': [{'name': mname, 'count': cnt} for mname, cnt in mods.most_common(5)],
            'exts': [{'name': e, 'count': cnt} for e, cnt in exts.most_common(5)],
        })
    return out


# ── neighbor edges ────────────────────────────────────────────────

def knn_edges(vectors, k: int = 4, min_sim: float = 0.55, max_edges: int = 40000):
    """Top-k cosine neighbors per node, deduped and thresholded."""
    import numpy as np
    X = np.asarray(vectors, dtype=np.float32)
    n = len(X)
    if n < 2:
        return []
    seen = set()
    edges = []
    BATCH = 512
    for start in range(0, n, BATCH):
        block = X[start:start + BATCH] @ X.T          # cosine — vectors are normalized
        for row_i, row in enumerate(block):
            i = start + row_i
            row[i] = -1.0
            top = np.argpartition(row, -min(k, n - 1))[-min(k, n - 1):]
            for j in top:
                s = float(row[j])
                if s < min_sim:
                    continue
                a, b = (i, int(j)) if i < j else (int(j), i)
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                edges.append({'s': a, 't': b, 'w': round(s, 3)})
    edges.sort(key=lambda e: -e['w'])
    return edges[:max_edges]


# ── the whole map ─────────────────────────────────────────────────

def build(vectors, metadata: List[dict], k: int = 0, method: str = 'tsne',
          neighbors: int = 4, min_sim: float = 0.55) -> Dict[str, Any]:
    """vectors + metadata -> {nodes, edges, clusters, modules, stats}."""
    import numpy as np

    X = np.asarray(vectors, dtype=np.float32)
    n = len(X)
    if n == 0:
        return {'nodes': [], 'edges': [], 'clusters': [], 'modules': [], 'count': 0}

    xy = layout(X, method=method)
    labels, k_used = cluster(X, k=k)
    clusters = label_clusters(labels, metadata, k_used)
    edges = knn_edges(X, k=neighbors, min_sim=min_sim)

    nodes = []
    for i, meta in enumerate(metadata):
        nodes.append({
            'i': i,
            'x': round(float(xy[i][0]), 4),
            'y': round(float(xy[i][1]), 4),
            'c': int(labels[i]),
            'rel': meta.get('rel', meta.get('path', '')),
            'mod': meta.get('module', ''),
            'ext': meta.get('ext', ''),
            'size': meta.get('size', 0),
            'lines': meta.get('lines', 0),
        })

    # per-module centroids, for the module-level overlay
    mod_agg: Dict[str, list] = {}
    for i, meta in enumerate(metadata):
        mod_agg.setdefault(meta.get('module', '?'), []).append(i)
    modules = []
    for name, idx in sorted(mod_agg.items(), key=lambda kv: -len(kv[1])):
        pts = xy[idx]
        modules.append({
            'name': name,
            'count': len(idx),
            'x': round(float(pts[:, 0].mean()), 4),
            'y': round(float(pts[:, 1].mean()), 4),
            'spread': round(float(np.linalg.norm(pts - pts.mean(axis=0), axis=1).mean()), 4),
            'bytes': int(sum(metadata[i].get('size', 0) for i in idx)),
        })

    ext_counts = Counter(m.get('ext', '') for m in metadata)
    return {
        'count': n,
        'nodes': nodes,
        'edges': edges,
        'clusters': clusters,
        'modules': modules,
        'exts': [{'name': e, 'count': c} for e, c in ext_counts.most_common()],
        'method': method,
        'k': k_used,
    }
