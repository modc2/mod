"""
grantfinder — a Claude agent that sweeps the web for grants and tags every one.

Starts with open source and crypto. A sweep runs each lane (a slice of the
landscape: OSS foundations, Ethereum & L2s, Bitcoin & privacy, ...) as one
Claude Code run, spawned from orbit/claudecode's harness spec and fenced to
WebSearch + WebFetch only. Results land in one SQLite file, merged by URL, and
are tagged by category, ecosystem, topic, mechanism, status and amount.

CLI:
    m grantfinder/sweep                          # every lane (spends the host's Claude account)
    m grantfinder/sweep category=crypto          # one category
    m grantfinder/sweep lanes=crypto-bitcoin,oss-security budget=1
    m grantfinder/grants tags=crypto,zk status=open
    m grantfinder/tags | m grantfinder/lanes | m grantfinder/runs
    m grantfinder/verify <id>                    # re-read one grant's official page
    m grantfinder/serve                          # REST + web app on :51380 (pm2 `grantfinder`)
    m grantfinder/status | m grantfinder/kill | m grantfinder/test
"""
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

MODULE_DIR = Path(__file__).resolve().parent
API_DIR = MODULE_DIR / 'src' / 'api'  # backend (grantscan + REST server); the web app is src/app
# Appended, never prepended: `mod` itself must keep winning.
if str(API_DIR) not in sys.path:
    sys.path.append(str(API_DIR))

from grantscan import agent, lanes as L, store, sweep as S  # noqa: E402

PM2_NAME = 'grantfinder'


class Mod:
    description = 'Claude agent that scrapes the web for open source + crypto grants, every one tagged'

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())
        self.port = int(self.config.get('port', 51380))

    def forward(self, query: str = '', **kwargs):
        return self.grants(q=query, **kwargs) if query else self.status()

    # ── reads ────────────────────────────────────────────────────────

    def grants(self, q: str = '', tags=None, status: str = '', sort: str = 'deadline', k: int = 0) -> list:
        """Stored grants. tags = 'crypto,zk' (all must match); status = open|rolling|upcoming|closed."""
        return store.grants(q, tags, status, sort, int(k))

    def grant(self, id: str) -> dict:
        return store.get(id)

    def tags(self) -> dict:
        """Tag counts grouped: category, ecosystem, topic, mechanism, status, amount, other."""
        return store.tag_counts()

    def lanes(self) -> list:
        return [{k: l[k] for k in ('id', 'category', 'title')} for l in L.LANES]

    def runs(self, sweep: str = '', k: int = 20) -> list:
        return [{k2: v for k2, v in r.items() if k2 != 'steps'} for r in store.runs(sweep, int(k))]

    # ── agent runs (spend) ───────────────────────────────────────────

    def sweep(self, lanes=None, category: str = None, model: str = 'sonnet',
              budget: float = 1.5, concurrency: int = 2) -> dict:
        """Run the agent over lanes (default all), blocking. budget = USD cap per lane."""
        return S.sweep(lanes, category, model, float(budget), int(concurrency))

    def verify(self, id: str, model: str = 'sonnet') -> dict:
        return S.verify(id, model)

    def retag(self) -> dict:
        """Re-tag every stored grant under the current tagger; fold same-site duplicates."""
        return store.retag()

    def delete(self, id: str) -> dict:
        return store.delete(id)

    # ── process ──────────────────────────────────────────────────────

    def serve(self, port: Optional[int] = None, host: str = '127.0.0.1') -> dict:
        port = int(port or self.port)
        subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        # cwd = src/api so `grantscan` imports; the server never imports `mod`.
        cmd = ['pm2', 'start', sys.executable, '--name', PM2_NAME, '--cwd', str(API_DIR),
               '--', '-m', 'grantscan.server', '--port', str(port), '--host', host]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'pm2': PM2_NAME, 'url': f'http://{host}:{port}/app',
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
            return {'running': False, 'error': str(e), 'hint': 'm grantfinder/serve',
                    **store.stats(), 'agent': agent.available()}

    def test(self) -> dict:
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test'], cwd=MODULE_DIR,
                           capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
