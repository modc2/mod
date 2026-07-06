"""
latexhub - Local filesystem LaTeX document storage and management

Stores .tex files on the local filesystem with metadata (tags, timestamps).
Supports compile to PDF via pdflatex/xelatex/lualatex.
"""

import os
import json
import time
import glob
import shutil
import subprocess
import tempfile
from typing import Optional, List, Dict, Any
from pathlib import Path


STORAGE_ROOT = os.environ.get(
    'LATEXHUB_STORAGE',
    os.path.join(os.path.dirname(os.path.dirname(__file__)), 'storage')
)


class Mod:
    description = "LaTeX document storage, compilation, and management hub"

    def __init__(self, storage_root: str = None, **kwargs):
        self.storage_root = storage_root or STORAGE_ROOT
        os.makedirs(self.storage_root, exist_ok=True)
        self._dir = os.path.dirname(os.path.dirname(__file__))

    # ── helpers ───────────────────────────────────────────────────────

    def _doc_dir(self, name: str, folder: str = None) -> str:
        parts = [self.storage_root]
        if folder:
            parts.append(folder)
        parts.append(name)
        return os.path.join(*parts)

    def _meta_path(self, doc_dir: str) -> str:
        return os.path.join(doc_dir, 'meta.json')

    def _tex_path(self, doc_dir: str) -> str:
        return os.path.join(doc_dir, 'main.tex')

    def _read_meta(self, doc_dir: str) -> dict:
        mp = self._meta_path(doc_dir)
        if os.path.exists(mp):
            with open(mp, 'r') as f:
                return json.load(f)
        return {}

    def _write_meta(self, doc_dir: str, meta: dict):
        with open(self._meta_path(doc_dir), 'w') as f:
            json.dump(meta, f, indent=2)

    # ── core functions ────────────────────────────────────────────────

    def save(self, name: str, content: str, folder: str = None,
             tags: List[str] = None) -> dict:
        doc_dir = self._doc_dir(name, folder)
        os.makedirs(doc_dir, exist_ok=True)

        tex_path = self._tex_path(doc_dir)
        with open(tex_path, 'w') as f:
            f.write(content)

        meta = self._read_meta(doc_dir)
        meta.update({
            'name': name,
            'folder': folder,
            'tags': tags or meta.get('tags', []),
            'updated_at': time.time(),
            'created_at': meta.get('created_at', time.time()),
            'size': len(content),
        })
        self._write_meta(doc_dir, meta)

        return {'status': 'saved', 'name': name, 'folder': folder, 'path': tex_path}

    def load(self, name: str, folder: str = None) -> dict:
        doc_dir = self._doc_dir(name, folder)
        tex_path = self._tex_path(doc_dir)

        if not os.path.exists(tex_path):
            return {'error': f'Document not found: {name}'}

        with open(tex_path, 'r') as f:
            content = f.read()

        meta = self._read_meta(doc_dir)
        return {'name': name, 'content': content, 'meta': meta}

    def ls(self, folder: str = None, tags: List[str] = None) -> list:
        search_root = os.path.join(self.storage_root, folder) if folder else self.storage_root
        if not os.path.exists(search_root):
            return []

        docs = []
        for entry in os.listdir(search_root):
            entry_path = os.path.join(search_root, entry)
            if not os.path.isdir(entry_path):
                continue

            meta_path = self._meta_path(entry_path)
            tex_path = self._tex_path(entry_path)

            # could be a folder containing docs, or a doc itself
            if os.path.exists(tex_path):
                meta = self._read_meta(entry_path)
                if tags:
                    doc_tags = meta.get('tags', [])
                    if not any(t in doc_tags for t in tags):
                        continue
                docs.append({
                    'name': entry,
                    'folder': folder,
                    **meta,
                })
            elif not folder:
                # recurse one level for folders
                sub_docs = self.ls(folder=entry, tags=tags)
                docs.extend(sub_docs)

        docs.sort(key=lambda d: d.get('updated_at', 0), reverse=True)
        return docs

    def rm(self, name: str, folder: str = None) -> dict:
        doc_dir = self._doc_dir(name, folder)
        if not os.path.exists(doc_dir):
            return {'error': f'Document not found: {name}'}

        shutil.rmtree(doc_dir)
        return {'status': 'deleted', 'name': name}

    def compile(self, name: str, folder: str = None,
                engine: str = 'pdflatex') -> dict:
        doc_dir = self._doc_dir(name, folder)
        tex_path = self._tex_path(doc_dir)

        if not os.path.exists(tex_path):
            return {'error': f'Document not found: {name}'}

        if engine not in ('pdflatex', 'xelatex', 'lualatex'):
            return {'error': f'Unsupported engine: {engine}'}

        if not shutil.which(engine):
            return {'error': f'{engine} not found on system. Install a TeX distribution.'}

        out_dir = os.path.join(doc_dir, 'out')
        os.makedirs(out_dir, exist_ok=True)

        try:
            result = subprocess.run(
                [engine, '-interaction=nonstopmode', '-output-directory', out_dir, tex_path],
                capture_output=True, text=True, timeout=120, cwd=doc_dir,
            )
            pdf_path = os.path.join(out_dir, 'main.pdf')
            if os.path.exists(pdf_path):
                return {
                    'status': 'compiled',
                    'name': name,
                    'pdf': pdf_path,
                    'engine': engine,
                }
            else:
                return {
                    'status': 'error',
                    'name': name,
                    'stdout': result.stdout[-2000:] if result.stdout else '',
                    'stderr': result.stderr[-2000:] if result.stderr else '',
                }
        except subprocess.TimeoutExpired:
            return {'status': 'error', 'name': name, 'error': 'Compilation timed out'}
        except Exception as e:
            return {'status': 'error', 'name': name, 'error': str(e)}

    def search(self, query: str, folder: str = None) -> list:
        docs = self.ls(folder=folder)
        query_lower = query.lower()
        results = []

        for doc in docs:
            # search name
            if query_lower in doc.get('name', '').lower():
                results.append(doc)
                continue
            # search tags
            if any(query_lower in t.lower() for t in doc.get('tags', [])):
                results.append(doc)
                continue
            # search content
            loaded = self.load(doc['name'], folder=doc.get('folder'))
            content = loaded.get('content', '')
            if query_lower in content.lower():
                # find snippet around match
                idx = content.lower().index(query_lower)
                start = max(0, idx - 80)
                end = min(len(content), idx + len(query) + 80)
                doc['snippet'] = content[start:end]
                results.append(doc)

        return results

    # ── serve / kill ──────────────────────────────────────────────────

    def serve(self, api_port=None, app_port=None, dev=True,
              api_only=False, app_only=False):
        """Start the latexhub API server (FastAPI) and/or the Next.js app."""
        api_port = api_port or 50200
        app_port = app_port or 3200
        results = {}

        if not app_only:
            results['api'] = self._serve_api(api_port, dev=dev)
        if not api_only:
            results['app'] = self._serve_app(app_port, dev=dev)

        return results

    def _serve_api(self, port, dev=True):
        mod_root = os.path.dirname(os.path.dirname(__file__))
        cwd = mod_root
        cmd = f'uvicorn api.api:app --host 0.0.0.0 --port {port}'
        if dev:
            cmd += ' --reload'

        script_path = os.path.join(mod_root, 'api', '_serve.sh')
        with open(script_path, 'w') as f:
            f.write(f'#!/bin/bash\ncd {cwd}\n{cmd}\n')
        os.chmod(script_path, 0o755)

        try:
            import mod as m
            pm2 = m.mod('pm.pm2')()
            name = 'latexhub-api'
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            return pm2.start_script(name=name, script_path=script_path,
                                    cwd=cwd, interpreter='bash')
        except Exception:
            proc = subprocess.Popen(['bash', script_path], cwd=cwd)
            return {'status': 'started', 'pid': proc.pid, 'port': port}

    def _serve_app(self, port, dev=True):
        mod_root = os.path.dirname(os.path.dirname(__file__))
        cwd = os.path.join(mod_root, 'app')
        cmd = f'npm run {"dev" if dev else "start"} -- -p {port}'

        script_path = os.path.join(cwd, '_serve.sh')
        with open(script_path, 'w') as f:
            f.write(f'#!/bin/bash\ncd {cwd}\n{cmd}\n')
        os.chmod(script_path, 0o755)

        try:
            import mod as m
            pm2 = m.mod('pm.pm2')()
            name = 'latexhub-app'
            if pm2.exists(name):
                pm2.kill(name, remove_script=False)
            return pm2.start_script(name=name, script_path=script_path,
                                    cwd=cwd, interpreter='bash')
        except Exception:
            proc = subprocess.Popen(['bash', script_path], cwd=cwd)
            return {'status': 'started', 'pid': proc.pid, 'port': port}

    def kill(self, service=None):
        """Stop running services. service: 'api', 'app', or None (both)."""
        results = {}
        try:
            import mod as m
            pm2 = m.mod('pm.pm2')()
            if service in (None, 'api') and pm2.exists('latexhub-api'):
                pm2.kill('latexhub-api')
                results['api'] = 'killed'
            if service in (None, 'app') and pm2.exists('latexhub-app'):
                pm2.kill('latexhub-app')
                results['app'] = 'killed'
        except Exception as e:
            results['error'] = str(e)
        return results or {'status': 'nothing running'}

    def status(self):
        """Check status of latexhub services."""
        results = {'module': 'latexhub'}
        try:
            import mod as m
            pm2 = m.mod('pm.pm2')()
            results['api'] = 'running' if pm2.exists('latexhub-api') else 'stopped'
            results['app'] = 'running' if pm2.exists('latexhub-app') else 'stopped'
        except Exception:
            results['api'] = 'unknown'
            results['app'] = 'unknown'

        docs = self.ls()
        results['doc_count'] = len(docs)
        results['storage'] = self.storage_root
        return results

    # ── aliases ───────────────────────────────────────────────────────
    forward = save
