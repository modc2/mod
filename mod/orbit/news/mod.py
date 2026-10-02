"""
news — ask for a topic, get the news about it, from everywhere, ranked.

Keyless and local-first: GDELT + Hacker News + your own RSS feeds by default
(reddit / google opt-in), merged, de-duplicated and ranked on this box.
Also an MCP server, so any agent can do the same.

CLI:
    m news/search "bittensor"                  # in-process, no server needed
    m news/search "EU AI act" k=5 hours=24 sources=gdelt,hn
    m news/read https://...                    # article text
    m news/feeds | m news/add_feed <url> | m news/remove_feed <url>
    m news/serve                               # REST + /mcp on :51120 (pm2 `news`)
    m news/status | m news/kill | m news/test

MCP:
    claude mcp add --transport http news http://localhost:51120/mcp
    claude mcp add news -- python3 -m newsagg.mcp_server     # stdio, cwd=orbit/news
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

from newsagg import cache, engine, mcp_server, sources, store  # noqa: E402

PM2_NAME = 'news'


def _list(v):
    if v is None or isinstance(v, list):
        return v
    return [s for s in str(v).split(',') if s]


class Mod:
    description = 'Keyless news aggregator for any query — GDELT, HN, RSS; ranked locally; MCP server'

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())
        self.port = int(self.config.get('port', 51120))

    def forward(self, query: str = '', **kwargs):
        return self.search(query, **kwargs) if query else self.status()

    def search(self, query: str, k: int = 20, hours: float = 72, sources=None) -> dict:
        """Ranked, de-duplicated stories for `query` (sources: list or 'gdelt,hn')."""
        return engine.search(query, k=int(k), hours=float(hours), sources=_list(sources))

    def headlines(self, query: str, k: int = 10, **kw) -> list:
        """Just the lines: '[outlet] title (age) url'."""
        res = self.search(query, k=k, **kw)['results']
        return [f"[{r['outlet']}] {r['title']} ({r['age_h']}h) {r['url']}" for r in res]

    def read(self, url: str, max_chars: int = 8000) -> dict:
        return engine.read(url, int(max_chars))

    def sources(self) -> dict:
        return {n: {'default': s['default'], 'docs': s['docs']} for n, s in sources.SOURCES.items()}

    def feeds(self) -> list:
        return store.feeds()

    def add_feed(self, url: str, name: str = '') -> dict:
        return store.add(url, name)

    def remove_feed(self, url: str) -> dict:
        return store.remove(url)

    def reset_feeds(self) -> dict:
        return store.reset()

    def clear_cache(self) -> dict:
        return cache.clear()

    def mcp(self) -> dict:
        """The MCP tools this module publishes."""
        return {'tools': mcp_server.tool_list(), 'http': f'http://localhost:{self.port}/mcp',
                'stdio': f'cd {MODULE_DIR} && python3 -m newsagg.mcp_server'}

    def serve(self, port: Optional[int] = None, host: str = '127.0.0.1') -> dict:
        port = int(port or self.port)
        subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        # cwd = module dir so `newsagg` imports; the server never imports `mod`.
        cmd = ['pm2', 'start', sys.executable, '--name', PM2_NAME, '--cwd', str(MODULE_DIR),
               '--', '-m', 'newsagg.server', '--port', str(port), '--host', host]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'pm2': PM2_NAME, 'url': f'http://{host}:{port}/health',
                'mcp': f'http://{host}:{port}/mcp', **({'error': r.stderr[-800:]} if r.returncode else {})}

    def kill(self) -> dict:
        r = subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        return {'killed': r.returncode == 0, 'pm2': PM2_NAME}

    def status(self) -> dict:
        import urllib.request
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=2) as r:
                return {'running': True, **json.loads(r.read())}
        except Exception as e:
            return {'running': False, 'error': str(e), 'hint': 'm news/serve', 'cache': cache.stats()}

    def test(self) -> dict:
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test'], cwd=MODULE_DIR,
                           capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
