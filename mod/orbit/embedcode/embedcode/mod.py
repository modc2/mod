"""
embedcode - generate and search embeddings of code using local models

Usage:
    import mod as m
    ec = m.mod('embedcode')()
    ec.forward('embed', path='./src')
    ec.forward('search', query='authentication logic', top_k=5)
    ec.forward('serve')
"""
import os
import json
import subprocess
import hashlib
import glob as globmod
from pathlib import Path
from typing import Dict, Any, Optional, List

try:
    import mod as m
    if hasattr(m, 'print'):
        print = m.print
except (ImportError, AttributeError):
    m = None


# ── file reading ──────────────────────────────────────────────────

CODE_EXTENSIONS = {
    '.py', '.js', '.ts', '.tsx', '.jsx', '.rs', '.go', '.java', '.c', '.cpp',
    '.h', '.hpp', '.cs', '.rb', '.php', '.swift', '.kt', '.scala', '.sh',
    '.bash', '.zsh', '.sql', '.html', '.css', '.scss', '.yaml', '.yml',
    '.toml', '.json', '.md', '.txt', '.sol', '.vy', '.cairo', '.move',
    '.r', '.lua', '.zig', '.nim', '.ex', '.exs', '.clj', '.ml', '.hs',
}

IGNORE_DIRS = {
    'node_modules', '.git', '__pycache__', '.next', 'dist', 'build',
    '.venv', 'venv', 'env', '.env', '.tox', 'target', 'out',
}

def collect_files(path: str, extensions: set = None) -> List[Dict[str, str]]:
    """Walk a directory and collect code files as {path, content} dicts."""
    extensions = extensions or CODE_EXTENSIONS
    path = os.path.expanduser(path)
    files = []

    if os.path.isfile(path):
        files.append({
            'path': path,
            'content': _read_file(path),
        })
        return files

    for root, dirs, fnames in os.walk(path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for fname in fnames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in extensions:
                fpath = os.path.join(root, fname)
                content = _read_file(fpath)
                if content:
                    files.append({'path': fpath, 'content': content})
    return files


def _read_file(path: str, max_bytes: int = 512_000) -> Optional[str]:
    try:
        if os.path.getsize(path) > max_bytes:
            return None
        with open(path, 'r', errors='ignore') as f:
            return f.read()
    except Exception:
        return None


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 64) -> List[str]:
    """Split text into overlapping chunks by lines."""
    lines = text.split('\n')
    chunks = []
    step = max(1, chunk_size - overlap)
    i = 0
    while i < len(lines):
        chunk_lines = lines[i:i + chunk_size]
        chunk = '\n'.join(chunk_lines)
        if chunk.strip():
            chunks.append(chunk)
        i += step
    return chunks or [text]


# ── embedding engine ──────────────────────────────────────────────

class EmbeddingEngine:
    """Local embedding model via sentence-transformers."""

    def __init__(self, model_name: str = 'all-MiniLM-L6-v2'):
        self.model_name = model_name
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: List[str]) -> List[List[float]]:
        """Embed a batch of texts, return list of vectors."""
        import numpy as np
        vecs = self.model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return vecs.tolist()

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]

    @property
    def dim(self) -> int:
        return self.model.get_sentence_embedding_dimension()


# ── vector store (numpy, no deps) ────────────────────────────────

class VectorStore:
    """Simple numpy-based vector store with cosine similarity search."""

    def __init__(self, store_path: str = None):
        self.store_path = store_path
        self.vectors = None       # np.ndarray (n, dim)
        self.metadata = []        # list of dicts
        if store_path and os.path.exists(store_path):
            self.load(store_path)

    def add(self, vectors: List[List[float]], metadata: List[dict]):
        import numpy as np
        new_vecs = np.array(vectors, dtype=np.float32)
        if self.vectors is None:
            self.vectors = new_vecs
        else:
            self.vectors = np.vstack([self.vectors, new_vecs])
        self.metadata.extend(metadata)

    def search(self, query_vec: List[float], top_k: int = 10) -> List[dict]:
        if self.vectors is None or len(self.vectors) == 0:
            return []
        import numpy as np
        q = np.array(query_vec, dtype=np.float32)
        # cosine similarity (vectors are already normalized)
        scores = self.vectors @ q
        top_idx = np.argsort(scores)[::-1][:top_k]
        results = []
        for i in top_idx:
            results.append({
                **self.metadata[i],
                'score': float(scores[i]),
            })
        return results

    def save(self, path: str = None):
        import numpy as np
        path = path or self.store_path
        os.makedirs(path, exist_ok=True)
        if self.vectors is not None:
            np.save(os.path.join(path, 'vectors.npy'), self.vectors)
        with open(os.path.join(path, 'metadata.json'), 'w') as f:
            json.dump(self.metadata, f)

    def load(self, path: str = None):
        import numpy as np
        path = path or self.store_path
        vecs_path = os.path.join(path, 'vectors.npy')
        meta_path = os.path.join(path, 'metadata.json')
        if os.path.exists(vecs_path):
            self.vectors = np.load(vecs_path)
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                self.metadata = json.load(f)

    @property
    def count(self) -> int:
        return len(self.metadata)

    def clear(self):
        self.vectors = None
        self.metadata = []


# ── Mod class ─────────────────────────────────────────────────────

class Mod:
    description = "Generate and search embeddings of code using local models"
    fns = [
        'forward', 'embed', 'search', 'collections', 'info',
        'delete', 'serve', 'app', 'kill', 'status',
    ]
    api_port = 8920
    app_port = 3920

    def __init__(self, model: str = 'all-MiniLM-L6-v2', store_dir: str = None, **kwargs):
        self._dir = os.path.dirname(__file__)
        self.model_name = model
        self.store_dir = store_dir or os.path.join(self._dir, '.store')
        self._engine = None
        self._stores = {}  # name -> VectorStore

    @property
    def engine(self) -> EmbeddingEngine:
        if self._engine is None:
            self._engine = EmbeddingEngine(self.model_name)
        return self._engine

    def _get_store(self, name: str) -> VectorStore:
        if name not in self._stores:
            path = os.path.join(self.store_dir, name)
            self._stores[name] = VectorStore(path)
        return self._stores[name]

    def _collection_name(self, path: str) -> str:
        """Derive a collection name from a path."""
        return hashlib.md5(os.path.abspath(os.path.expanduser(path)).encode()).hexdigest()[:12]

    # ── Core functions ──

    def embed(self, path: str, collection: str = None, chunk_size: int = 512,
              overlap: int = 64, extensions: List[str] = None) -> dict:
        """Embed all code files in a path into a named collection."""
        path = os.path.expanduser(path)
        name = collection or self._collection_name(path)
        ext_set = set(extensions) if extensions else None
        files = collect_files(path, ext_set)

        if not files:
            return {'error': 'No code files found', 'path': path}

        store = self._get_store(name)
        total_chunks = 0
        batch_texts = []
        batch_meta = []

        for f in files:
            chunks = chunk_text(f['content'], chunk_size, overlap)
            for i, chunk in enumerate(chunks):
                batch_texts.append(chunk)
                batch_meta.append({
                    'path': f['path'],
                    'chunk_index': i,
                    'total_chunks': len(chunks),
                    'preview': chunk[:200],
                })
            total_chunks += len(chunks)

        # embed in batches of 64
        BATCH = 64
        for i in range(0, len(batch_texts), BATCH):
            vecs = self.engine.embed(batch_texts[i:i+BATCH])
            store.add(vecs, batch_meta[i:i+BATCH])

        store.save()

        return {
            'collection': name,
            'path': os.path.abspath(path),
            'files': len(files),
            'chunks': total_chunks,
            'model': self.model_name,
            'dim': self.engine.dim,
        }

    def search(self, query: str, collection: str = None, path: str = None,
               top_k: int = 10) -> List[dict]:
        """Search embeddings by natural language query."""
        if path and not collection:
            collection = self._collection_name(path)
        if not collection:
            # search all collections
            results = []
            for name in self.collections()['collections']:
                results.extend(self._search_collection(query, name, top_k))
            results.sort(key=lambda x: x['score'], reverse=True)
            return results[:top_k]
        return self._search_collection(query, collection, top_k)

    def _search_collection(self, query: str, collection: str, top_k: int) -> List[dict]:
        store = self._get_store(collection)
        if store.count == 0:
            return []
        qvec = self.engine.embed_one(query)
        results = store.search(qvec, top_k)
        for r in results:
            r['collection'] = collection
        return results

    def collections(self) -> dict:
        """List all embedding collections."""
        cols = []
        if os.path.exists(self.store_dir):
            for name in os.listdir(self.store_dir):
                meta_path = os.path.join(self.store_dir, name, 'metadata.json')
                if os.path.exists(meta_path):
                    with open(meta_path) as f:
                        meta = json.load(f)
                    paths = list(set(item.get('path', '') for item in meta))
                    cols.append({
                        'name': name,
                        'chunks': len(meta),
                        'files': len(paths),
                        'paths': paths[:10],
                    })
        return {'collections': [c['name'] for c in cols], 'details': cols}

    def info(self, collection: str = None) -> dict:
        """Get info about a collection or the module."""
        if collection:
            store = self._get_store(collection)
            return {
                'collection': collection,
                'chunks': store.count,
                'model': self.model_name,
            }
        return {
            'module': 'embedcode',
            'model': self.model_name,
            'store_dir': self.store_dir,
            'collections': self.collections(),
        }

    def delete(self, collection: str) -> dict:
        """Delete a collection."""
        import shutil
        path = os.path.join(self.store_dir, collection)
        if os.path.exists(path):
            shutil.rmtree(path)
            if collection in self._stores:
                del self._stores[collection]
            return {'deleted': collection}
        return {'error': f'Collection {collection} not found'}

    # ── Forward (generic dispatch) ──

    def forward(self, fn: str = None, **kwargs) -> Any:
        """Generic function dispatch — call any fn by name."""
        if fn is None:
            return {'module': 'embedcode', 'fns': self.fns}
        if not hasattr(self, fn) or fn.startswith('_'):
            raise ValueError(f"unknown fn: {fn}")
        return getattr(self, fn)(**kwargs)

    # ── Serve / Kill / Status ──

    def serve(self, port=None, api_port=None, app_port=None, dev=True):
        """Start the embedcode API and app."""
        api_p = api_port or port or self.api_port
        app_p = app_port or self.app_port

        # Start API
        cwd = self._dir
        cmd = f'uvicorn api:app --host 0.0.0.0 --port {api_p}'
        if dev:
            cmd += ' --reload'

        script = os.path.join(self._dir, '_serve.sh')
        with open(script, 'w') as f:
            f.write(f'#!/bin/bash\ncd {cwd}\n{cmd}\n')
        os.chmod(script, 0o755)

        try:
            pm2 = m.mod('pm.pm2')()
            if pm2.exists('embedcode-api'):
                pm2.kill('embedcode-api', remove_script=False)
            pm2.start_script(name='embedcode-api', script_path=script, cwd=cwd, interpreter='bash')
        except Exception:
            subprocess.Popen(['bash', script], cwd=cwd)

        # Start App
        self.app(port=app_p, dev=dev)

        return {
            'status': 'running',
            'port': api_p,
            'app_port': app_p,
            'urls': {
                'api': f'http://localhost:{api_p}',
                'app': f'http://localhost:{app_p}',
            },
            'url': f'http://localhost:{api_p}',
        }

    def app(self, port=None, dev=True):
        """Start the embedcode web app."""
        port = port or self.app_port
        cwd = os.path.join(self._dir, 'app')
        if not os.path.exists(cwd):
            return {'error': 'App directory not found'}

        cmd = f'npm run dev -- -p {port}' if dev else f'npm run start -- -p {port}'
        script = os.path.join(self._dir, '_app.sh')
        with open(script, 'w') as f:
            f.write(f'#!/bin/bash\ncd {cwd}\n{cmd}\n')
        os.chmod(script, 0o755)

        try:
            pm2 = m.mod('pm.pm2')()
            name = 'embedcode-app'
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            pm2.start_script(name=name, script_path=script, cwd=cwd, interpreter='bash')
            return {'status': 'running', 'port': port, 'manager': 'pm2',
                    'url': f'http://localhost:{port}'}
        except Exception:
            proc = subprocess.Popen(['bash', script], cwd=cwd)
            return {'status': 'running', 'port': port, 'manager': 'subprocess',
                    'pid': proc.pid, 'url': f'http://localhost:{port}'}

    def kill(self, target='all'):
        """Stop embedcode services (api/app/all)."""
        results = {}
        try:
            pm2 = m.mod('pm.pm2')()
            if target in ('api', 'all'):
                if pm2.exists('embedcode-api'):
                    pm2.kill('embedcode-api')
                    results['api'] = 'stopped'
            if target in ('app', 'all'):
                if pm2.exists('embedcode-app'):
                    pm2.kill('embedcode-app')
                    results['app'] = 'stopped'
        except Exception as e:
            results['error'] = str(e)
        return results

    def status(self):
        """Check service status."""
        result = {'module': 'embedcode', 'model': self.model_name}
        try:
            pm2 = m.mod('pm.pm2')()
            result['api'] = 'running' if pm2.exists('embedcode-api') else 'stopped'
            result['app'] = 'running' if pm2.exists('embedcode-app') else 'stopped'
        except Exception:
            result['api'] = 'unknown'
            result['app'] = 'unknown'
        cols = self.collections()
        result['collections'] = len(cols.get('collections', []))
        return result
