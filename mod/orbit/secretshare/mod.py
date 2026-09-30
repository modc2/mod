"""
secretshare — split a secret into N pieces, any K of which bring it back.

Shamir's Secret Sharing over GF(256), pure python (shamir/) with a byte-for-byte
compatible browser twin (app/shamir.js). Local-first: nothing is stored, no
store/chain/network dependency; the console splits inside the browser tab.

CLI:
    m secretshare/split "correct horse battery" n=5 k=3
    m secretshare/split path=./seed.txt n=3 k=2
    m secretshare/combine "ss1.… ss1.… ss1.…"
    m secretshare/combine path=./pieces.txt
    m secretshare/inspect ss1.…
    m secretshare/test                   # roundtrip self-check
    m secretshare/serve                  # api + console on :51080, route /secretshare
    m secretshare/kill
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import mod as m

MODULE_DIR = Path(__file__).resolve().parent
# Appended, never prepended: `mod` itself must keep winning.
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

import shamir  # noqa: E402

PM2_NAME = 'secretshare'


class Mod:
    description = 'Split a secret into N pieces so any K of them rebuild it (Shamir, GF(256)); local-first, stores nothing'

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())
        self.port = int(self.config.get('port', 51080))

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        return {'name': self.config['name'], 'description': self.description,
                'port': self.port, 'url': f'http://localhost:{self.port}/secretshare/',
                'format': f'{shamir.VERSION}.<set>.<k>.<x>.<payload>',
                'fns': ['split', 'combine', 'inspect', 'test', 'serve', 'kill', 'status']}

    # ── shares ───────────────────────────────────────────────────────

    def split(self, secret: Optional[str] = None, n: int = 5, k: int = 3,
              path: Optional[str] = None, out: Optional[str] = None) -> dict:
        """Split text (or a file's bytes) into n shares, threshold k.
        out=<dir> writes one file per share so they can be handed out."""
        if path:
            secret = Path(os.path.expanduser(path)).read_bytes()
        if secret is None:
            return {'error': 'pass the secret, or path=<file>'}
        shares = shamir.split(secret, n=int(n), k=int(k))
        res = {'n': int(n), 'k': int(k), 'set': shares[0].split('.')[1], 'shares': shares}
        if out:
            d = Path(os.path.expanduser(out))
            d.mkdir(parents=True, exist_ok=True)
            files = []
            for i, s in enumerate(shares, 1):
                f = d / f'secretshare-{res["set"]}-piece-{i}-of-{n}.txt'
                f.write_text(s + '\n')
                os.chmod(f, 0o600)
                files.append(str(f))
            res['files'] = files
        return res

    def combine(self, shares=None, path: Optional[str] = None,
                out: Optional[str] = None) -> dict:
        """Rebuild a secret from k shares — a list, a whitespace/comma separated
        string, or path=<file|dir> holding them."""
        if path:
            p = Path(os.path.expanduser(path))
            files = sorted(p.glob('*.txt')) if p.is_dir() else [p]
            shares = ' '.join(f.read_text() for f in files)
        if not shares:
            return {'error': 'pass shares, or path=<file|dir>'}
        data = shamir.combine(shares)
        if out:
            Path(os.path.expanduser(out)).write_bytes(data)
            return {'out': out, 'bytes': len(data)}
        try:
            return {'secret': data.decode(), 'bytes': len(data)}
        except UnicodeDecodeError:
            import base64
            return {'secret_b64': base64.b64encode(data).decode(), 'bytes': len(data)}

    def inspect(self, share: str) -> dict:
        """Set id, threshold and index of a share — reveals nothing of the secret."""
        return shamir.inspect(share)

    def test(self) -> dict:
        """Roundtrip self-check: every k-subset rebuilds, k-1 fails."""
        from itertools import combinations
        secret = os.urandom(40)
        shares = shamir.split(secret, n=5, k=3)
        ok = all(shamir.combine(list(c)) == secret for c in combinations(shares, 3))
        try:
            shamir.combine(shares[:2])
            short_fails = False
        except shamir.ShareError:
            short_fails = True
        return {'ok': ok and short_fails, 'subsets_rebuild': ok, 'k_minus_1_refused': short_fails}

    # ── serve ────────────────────────────────────────────────────────

    def serve(self, port: Optional[int] = None) -> dict:
        """Start api + console (one uvicorn process) under pm2."""
        port = int(port or self.port)
        repo_root = str(self.module_dir.parent.parent.parent)
        subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        # cwd = repo root: `python -m uvicorn` puts cwd on sys.path[0], and this
        # module's mod.py would shadow the `mod` package from the module dir.
        cmd = ['pm2', 'start', 'python3', '--name', PM2_NAME, '--cwd', repo_root, '--',
               '-m', 'uvicorn', 'api:app', '--host', '0.0.0.0', '--port', str(port),
               '--app-dir', str(self.module_dir / 'api')]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'pm2': PM2_NAME,
                'console': f'http://localhost:{port}/secretshare/',
                'api': f'http://localhost:{port}/secretshare/api/health',
                'gateway': 'https://modc2.com/secretshare',
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
            return {'running': False, 'error': str(e), 'hint': 'm secretshare/serve'}

    def readme(self):
        return m.get_text(str(self.module_dir / 'README.md'))
