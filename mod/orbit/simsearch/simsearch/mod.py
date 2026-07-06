"""
simsearch - semantic similarity search across orbit modules

Embeds each orbit module's source code, then finds the most relevant
modules for a query using cosine similarity between embeddings.

Usage:
    import mod as m
    ss = m.mod('simsearch')()
    ss.embed()                          # embed all orbit modules
    ss.search('blockchain deployment')  # cosine similarity search
"""
import os
import json
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    import mod as m
    if hasattr(m, 'print'):
        print = m.print
except (ImportError, AttributeError):
    m = None


# ── constants ────────────────────────────────────────────────────

CODE_EXTENSIONS = {
    '.py', '.js', '.ts', '.tsx', '.jsx', '.rs', '.go', '.sol',
    '.sh', '.sql', '.yaml', '.yml', '.toml', '.json', '.md',
}

IGNORE_DIRS = {
    'node_modules', '.git', '__pycache__', '.next', 'dist', 'build',
    '.venv', 'venv', 'env', '.env', '.tox', 'target', 'out', '.store',
}


# ── helpers ──────────────────────────────────────────────────────

def _read_file(path: str, max_bytes: int = 256_000) -> Optional[str]:
    try:
        if os.path.getsize(path) > max_bytes:
            return None
        with open(path, 'r', errors='ignore') as f:
            return f.read()
    except Exception:
        return None


def collect_module_files(mod_dir: str) -> List[Dict[str, str]]:
    """Collect code files from an orbit module directory."""
    files = []
    for root, dirs, fnames in os.walk(mod_dir):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for fname in fnames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in CODE_EXTENSIONS:
                fpath = os.path.join(root, fname)
                content = _read_file(fpath)
                if content:
                    files.append({'path': fpath, 'content': content})
    return files


def chunk_text(text: str, chunk_size: int = 256, overlap: int = 32) -> List[str]:
    """Split text into overlapping line-based chunks."""
    lines = text.split('\n')
    chunks = []
    step = max(1, chunk_size - overlap)
    i = 0
    while i < len(lines):
        chunk = '\n'.join(lines[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
        i += step
    return chunks or [text]


# ── embedding engine ─────────────────────────────────────────────

class EmbeddingEngine:
    """Local embeddings via sentence-transformers."""

    def __init__(self, model_name: str = 'all-MiniLM-L6-v2'):
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: List[str]):
        """Embed batch, return numpy array (normalized)."""
        return self.model.encode(texts, show_progress_bar=False, normalize_embeddings=True)

    def embed_one(self, text: str):
        return self.embed([text])[0]

    @property
    def dim(self) -> int:
        return self.model.get_embedding_dimension()


# ── vector store ─────────────────────────────────────────────────

class VectorStore:
    """Numpy cosine similarity store."""

    def __init__(self, path: str = None):
        self.path = path
        self.vectors = None
        self.metadata = []
        if path and os.path.exists(os.path.join(path, 'vectors.npy')):
            self.load(path)

    def add(self, vectors, metadata: List[dict]):
        import numpy as np
        new = np.array(vectors, dtype=np.float32) if not hasattr(vectors, 'dtype') else vectors.astype(np.float32)
        self.vectors = new if self.vectors is None else np.vstack([self.vectors, new])
        self.metadata.extend(metadata)

    def search(self, query_vec, top_k: int = 10) -> List[dict]:
        if self.vectors is None or len(self.vectors) == 0:
            return []
        import numpy as np
        q = np.array(query_vec, dtype=np.float32)
        scores = self.vectors @ q
        top_idx = np.argsort(scores)[::-1][:top_k]
        return [{**self.metadata[i], 'score': float(scores[i])} for i in top_idx]

    def save(self, path: str = None):
        import numpy as np
        path = path or self.path
        os.makedirs(path, exist_ok=True)
        if self.vectors is not None:
            np.save(os.path.join(path, 'vectors.npy'), self.vectors)
        with open(os.path.join(path, 'metadata.json'), 'w') as f:
            json.dump(self.metadata, f)

    def load(self, path: str = None):
        import numpy as np
        path = path or self.path
        vp = os.path.join(path, 'vectors.npy')
        mp = os.path.join(path, 'metadata.json')
        if os.path.exists(vp):
            self.vectors = np.load(vp)
        if os.path.exists(mp):
            with open(mp) as f:
                self.metadata = json.load(f)

    @property
    def count(self) -> int:
        return len(self.metadata)

    def clear(self):
        self.vectors = None
        self.metadata = []


# ── Mod class ────────────────────────────────────────────────────

class Mod:
    description = "Semantic similarity search across orbit modules using code embeddings"

    def __init__(self, model: str = 'all-MiniLM-L6-v2', **kwargs):
        self._dir = os.path.dirname(__file__)
        self._orbit = os.path.abspath(os.path.join(self._dir, '..', '..'))
        self.store_dir = os.path.join(self._dir, '.store')
        self.model_name = model
        self._engine = None
        self._store = None

    @property
    def engine(self) -> EmbeddingEngine:
        if self._engine is None:
            self._engine = EmbeddingEngine(self.model_name)
        return self._engine

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            self._store = VectorStore(os.path.join(self.store_dir, 'modules'))
        return self._store

    # ── discover modules ──

    def _discover_modules(self) -> Dict[str, str]:
        """Return {name: path} for all orbit modules."""
        modules = {}
        for entry in sorted(os.listdir(self._orbit)):
            mod_dir = os.path.join(self._orbit, entry)
            if not os.path.isdir(mod_dir) or entry.startswith('.'):
                continue
            # look for anchor mod.py in <name>/<name>/mod.py or <name>/src/mod.py
            for sub in [entry, 'src']:
                anchor = os.path.join(mod_dir, sub, 'mod.py')
                if os.path.exists(anchor):
                    modules[entry] = mod_dir
                    break
        return modules

    # ── embed ──

    def embed(self, modules: List[str] = None, chunk_size: int = 256, overlap: int = 32) -> dict:
        """Embed orbit module source code.

        Args:
            modules: list of module names to embed (default: all)
            chunk_size: lines per chunk
            overlap: overlap lines between chunks
        """
        all_mods = self._discover_modules()
        if modules:
            all_mods = {k: v for k, v in all_mods.items() if k in modules}

        if not all_mods:
            return {'error': 'No modules found'}

        # reset store
        self.store.clear()

        total_files = 0
        total_chunks = 0
        batch_texts = []
        batch_meta = []

        for mod_name, mod_dir in all_mods.items():
            files = collect_module_files(mod_dir)
            if not files:
                continue

            for f in files:
                rel_path = os.path.relpath(f['path'], self._orbit)
                chunks = chunk_text(f['content'], chunk_size, overlap)
                for i, chunk in enumerate(chunks):
                    batch_texts.append(chunk)
                    batch_meta.append({
                        'module': mod_name,
                        'path': rel_path,
                        'chunk_index': i,
                        'preview': chunk[:200],
                    })
                total_chunks += len(chunks)
            total_files += len(files)
            print(f'  {mod_name}: {len(files)} files')

        if not batch_texts:
            return {'error': 'No code found in modules'}

        # embed in batches
        BATCH = 64
        for i in range(0, len(batch_texts), BATCH):
            vecs = self.engine.embed(batch_texts[i:i+BATCH])
            self.store.add(vecs, batch_meta[i:i+BATCH])
            print(f'  embedded {min(i+BATCH, len(batch_texts))}/{len(batch_texts)} chunks')

        self.store.save()

        return {
            'modules': len(all_mods),
            'files': total_files,
            'chunks': total_chunks,
            'model': self.model_name,
            'dim': self.engine.dim,
        }

    # ── search ──

    def search(self, query: str, top_k: int = 10, unique_modules: bool = False) -> List[dict]:
        """Search modules by cosine similarity to query embedding.

        Args:
            query: natural language search query
            top_k: number of results
            unique_modules: if True, return only top hit per module
        """
        if self.store.count == 0:
            print('No embeddings found — run embed() first')
            self.embed()

        q_vec = self.engine.embed_one(query)
        results = self.store.search(q_vec, top_k=top_k * 3 if unique_modules else top_k)

        if unique_modules:
            seen = set()
            unique = []
            for r in results:
                if r['module'] not in seen:
                    seen.add(r['module'])
                    unique.append(r)
                if len(unique) >= top_k:
                    break
            return unique

        return results[:top_k]

    # ── module search (convenience) ──

    def find(self, query: str, n: int = 5) -> List[dict]:
        """Find the top-n most relevant modules for a query.

        Returns one result per module, ranked by best matching chunk.
        """
        results = self.search(query, top_k=n, unique_modules=True)
        return [{
            'module': r['module'],
            'score': round(r['score'], 4),
            'path': r['path'],
            'preview': r['preview'][:120],
        } for r in results]

    # ── info ──

    def info(self) -> dict:
        """Show index stats."""
        modules = self._discover_modules()
        indexed = set(item.get('module') for item in self.store.metadata) if self.store.count else set()
        return {
            'modules_available': len(modules),
            'modules_indexed': len(indexed),
            'chunks': self.store.count,
            'model': self.model_name,
            'store': self.store_dir,
        }

    def modules(self) -> List[str]:
        """List all discoverable orbit modules."""
        return sorted(self._discover_modules().keys())

    # ── forward ──

    def forward(self, query: str = None, n: int = 5, **kwargs) -> Any:
        """Search for modules matching a query (default entry point).

        Args:
            query: what to search for
            n: number of results
        """
        if query is None:
            return self.info()
        return self.find(query, n=n)
