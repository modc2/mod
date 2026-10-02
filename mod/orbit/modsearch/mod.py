"""
modsearch — find a module by what you mean.

Local semantic search: a small sentence encoder (all-MiniLM-L6-v2, CPU) fused
with BM25, vectors cached on disk. No key, no cloud, no LLM call — falls back
to BM25 alone if the encoder can't load.

CLI:
    m modsearch/search "chart my bittensor portfolio"
    m modsearch/search "split a secret" k=3
    m modsearch/serve        # api on 127.0.0.1:51090 (pm2 `modsearch`)
    m modsearch/status
    m modsearch/kill

HTTP (what the build hub's agent search calls):
    POST /search {query, docs:[{id,text}], k}  → {mode, results:[{id,score,sem,lex,why}]}
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

MODULE_DIR = Path(__file__).resolve().parent
# Appended, never prepended: `mod` itself must keep winning.
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

import modsearch as engine  # noqa: E402

PM2_NAME = 'modsearch'


class Mod:
    description = 'Semantic search over the module fleet (or any docs you send) — local MiniLM + BM25, no key'

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())
        self.port = int(self.config.get('port', 51090))
        self._encoder = None

    def forward(self, query: str = '', **kwargs):
        return self.search(query, **kwargs) if query else self.status()

    def search(self, query: str, k: int = 10, docs: Optional[list] = None) -> dict:
        """Rank modules (or `docs`=[{id,text}]) by meaning. In-process — no server needed."""
        if self._encoder is None:
            self._encoder = engine.Encoder()
        roots = [MODULE_DIR.parent, MODULE_DIR.parent.parent / 'core']
        return engine.search(query, docs if docs is not None else engine.fleet_docs(roots),
                             k=int(k), encoder=self._encoder)

    def serve(self, port: Optional[int] = None, host: str = '127.0.0.1') -> dict:
        port = int(port or self.port)
        subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        # cwd = repo root so this mod.py never shadows the `mod` package.
        cmd = ['pm2', 'start', 'python3', '--name', PM2_NAME,
               '--cwd', str(MODULE_DIR.parent.parent.parent), '--',
               '-m', 'uvicorn', 'api:app', '--host', host, '--port', str(port),
               '--app-dir', str(MODULE_DIR / 'api')]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'pm2': PM2_NAME, 'url': f'http://{host}:{port}/health',
                **({'error': r.stderr[-800:]} if r.returncode else {})}

    def kill(self) -> dict:
        r = subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        return {'killed': r.returncode == 0, 'pm2': PM2_NAME}

    def status(self) -> dict:
        import urllib.request
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=2) as r:
                return {'running': True, **json.loads(r.read())}
        except Exception as e:
            return {'running': False, 'error': str(e), 'hint': 'm modsearch/serve'}

    def test(self) -> dict:
        import pytest
        return {'exit': int(pytest.main(['-q', str(MODULE_DIR / 'test')]))}
