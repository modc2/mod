"""
artlist — search Artlist.io's royalty-free catalog (music, SFX, footage) from here.

Keyless for search, metadata and AAC previews, via Artlist's public search
GraphQL (search-api.artlist.io). Licensed downloads stay on your own Artlist
account: drop a bearer in ~/.mod/artlist/token and use gq(auth=True).

CLI:
    m artlist/music "epic cinematic"            # in-process, no server needed
    m artlist/music "lofi study" k=5 page=2
    m artlist/song 89458                        # full record(s) by id
    m artlist/preview 89458                     # AAC preview -> ~/.mod/artlist/previews/
    m artlist/gq '{ songList(...) { ... } }'    # raw GraphQL escape hatch
    m artlist/serve                             # REST + /mcp on :51190 (pm2 `artlist`)
    m artlist/status | m artlist/kill | m artlist/test

MCP:
    claude mcp add --transport http artlist http://localhost:51190/mcp
    claude mcp add artlist -- python3 -m artlistapi.mcp_server   # stdio, cwd=orbit/artlist
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

from artlistapi import api, client, mcp_server  # noqa: E402

PM2_NAME = 'artlist'


def _ids(v) -> list:
    if isinstance(v, (list, tuple)):
        return [str(i) for i in v]
    return [s for s in str(v).split(',') if s]


class Mod:
    description = 'Artlist.io royalty-free music/SFX/footage search — keyless previews; MCP server'

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())
        self.port = int(self.config.get('port', 51190))

    def forward(self, query: str = '', **kwargs):
        return self.music(query, **kwargs) if query else self.status()

    def music(self, query: str = '', k: int = 10, page: int = 1, sort: int = 1, vocal: int = 0) -> dict:
        """Search songs. Returns {total, results:[{id,name,artist,duration,preview_url,tags,page_url}]}."""
        return api.music(query, k=int(k), page=int(page), sort=int(sort), vocal=int(vocal))

    def sfx(self, query: str = '', k: int = 10, page: int = 1, sort: str = 'NEWEST') -> dict:
        """Search sound effects. sort: NEWEST | TOP_DOWNLOADS | STAFF_PICKS."""
        return api.sfx(query, k=int(k), page=int(page), sort=sort)

    def footage(self, query: str = '', k: int = 10, page: int = 1) -> dict:
        return api.footage(query, k=int(k), page=int(page))

    def templates(self, query: str = '', k: int = 10, page: int = 1, sort: str = 'TOP_DOWNLOADS') -> dict:
        return api.templates(query, k=int(k), page=int(page), sort=sort)

    def voices(self, page: int = 1, k: int = 10) -> dict:
        """AI voiceover voices; each accent carries a playable preview_url."""
        return api.voices(page=int(page), k=int(k))

    def song(self, ids) -> list:
        """One or more songs by id (comma-string or list)."""
        return api.songs(_ids(ids))

    def album(self, ids) -> list:
        return api.albums(_ids(ids))

    def artist(self, ids) -> list:
        return api.artist(_ids(ids))

    def clip(self, clip_id) -> dict:
        return api.clip(int(clip_id))

    def story(self, story_id, page: int = 1) -> dict:
        """A footage story (shoot) and its clips."""
        return api.story(int(story_id), page=int(page))

    def preview(self, song_id, out: Optional[str] = None) -> dict:
        """Download a song's AAC preview from Artlist's CDN (artlist.io hosts only)."""
        recs = api.songs([str(song_id)])
        if not recs or not recs[0].get('preview_url'):
            return {'ok': False, 'error': f'no preview for song {song_id}'}
        dest = Path(out) if out else client.STATE_DIR / 'previews' / f'{song_id}.aac'
        client.fetch_preview(recs[0]['preview_url'], dest)
        return {'ok': True, 'path': str(dest), 'song': recs[0]['name'], 'artist': recs[0]['artist']}

    def gq(self, query: str, variables: Optional[dict] = None, auth: bool = False) -> dict:
        """Raw GraphQL passthrough. auth=True attaches your ~/.mod/artlist/token bearer."""
        return client.gql(query, variables, auth=bool(auth))

    def clear_cache(self) -> dict:
        return client.clear_cache()

    def mcp(self) -> dict:
        """The MCP tools this module publishes."""
        return {'tools': mcp_server.tool_list(), 'http': f'http://localhost:{self.port}/mcp',
                'stdio': f'cd {MODULE_DIR} && python3 -m artlistapi.mcp_server'}

    def serve(self, port: Optional[int] = None, host: str = '127.0.0.1') -> dict:
        port = int(port or self.port)
        subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        # cwd = module dir so `artlistapi` imports; the server never imports `mod`.
        cmd = ['pm2', 'start', sys.executable, '--name', PM2_NAME, '--cwd', str(MODULE_DIR),
               '--', '-m', 'artlistapi.server', '--port', str(port), '--host', host]
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
            return {'running': False, 'error': str(e), 'hint': 'm artlist/serve',
                    'cache': client.cache_stats()}

    def test(self) -> dict:
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test'], cwd=MODULE_DIR,
                           capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
