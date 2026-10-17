"""
youtube — YouTube in a frame, under any account, with an MCP server that acts
as the user and as the creator.

The first page IS YouTube: a full-width embed frame playing the home feed
(the active account's subscriptions, else trending, else a keyless channel
feed), a search bar, and a grid that loads anything into the frame.
youtube.com itself sends X-Frame-Options: SAMEORIGIN, so what gets framed is
always the /embed/ form — `open` rewrites any YouTube address into it.

Accounts are Google OAuth (your own Google Cloud client); any number can be
signed in and switched. Tokens live in ~/.mod/youtube/ (never config.json).

CLI:
    m youtube/open https://youtu.be/dQw4w9WgXcQ        # -> framable embed url
    m youtube/open @veritasium                         # channel -> uploads playlist embed
    m youtube/home | m youtube/rss @nasa               # keyless feeds
    m youtube/search "lofi" n=5                        # Data API (api key or account)
    m youtube/set_client client_id=... client_secret=... api_key=...
    m youtube/login                                    # prints the Google consent url (server must run)
    m youtube/accounts | m youtube/use <id|email|@handle>
    m youtube/feed | m youtube/subscriptions | m youtube/like <video> | m youtube/subscribe @x
    m youtube/me | m youtube/my_videos | m youtube/upload path=clip.mp4 title="..." privacy=private
    m youtube/analytics start=2026-10-01 end=2026-10-15
    m youtube/serve                                    # app + REST + /mcp on :51400 (pm2 `youtube`)
    m youtube/status | m youtube/kill | m youtube/test

MCP:
    claude mcp add --transport http youtube http://localhost:51400/mcp
    claude mcp add youtube -- python3 -m ytmod.mcp_server      # stdio, cwd=orbit/youtube
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional

MODULE_DIR = Path(__file__).resolve().parent
# Appended, never prepended: `mod` itself must keep winning.
if str(MODULE_DIR) not in sys.path:
    sys.path.append(str(MODULE_DIR))

from ytmod import api, auth, mcp_server, store  # noqa: E402

PM2_NAME = 'youtube'


def _list(v):
    if v is None or isinstance(v, list):
        return v
    return [s for s in str(v).split(',') if s]


class Mod:
    description = 'YouTube in a frame under any Google account + MCP server as user and creator'

    def __init__(self):
        self.module_dir = MODULE_DIR
        self.config = json.loads((MODULE_DIR / 'config.json').read_text())
        self.port = int(self.config.get('port', 51400))

    # the CLI runs on the box, so it is the owner
    def _s(self, account: Optional[str] = None) -> api.Session:
        return api.Session(owner=True, account=account)

    def forward(self, target: str = '', **kwargs):
        return self.open(target) if target else self.status()

    # ── keyless ──
    def open(self, target: str) -> dict:
        """Any YouTube url / id / @handle -> {kind, id, embed, watch}. `embed` is the framable one."""
        return api.resolve(target)

    def embed(self, target: str) -> str:
        """Just the framable url."""
        return api.resolve(target).get('embed') or ''

    def rss(self, channel: str, n: int = 15) -> dict:
        """A channel's newest uploads from its public feed. Zero quota, no key."""
        return api.rss(channel, int(n))

    def home(self, n: int = 24, account: Optional[str] = None) -> dict:
        """The home page: subscriptions feed as the active account, else trending, else keyless channels."""
        return mcp_server.TOOLS['yt_home']['fn']({'owner': True}, {'n': int(n), 'account': account})

    def home_channels(self, ids=None) -> list:
        """Get / set the channels the keyless home feed merges (comma list of UC ids)."""
        return store.set_home_channels(_list(ids)) if ids else store.home_channels()

    # ── reads (api key or account) ──
    def search(self, q: str, kind: str = 'video', n: int = 12, order: str = 'relevance',
               channel_id: Optional[str] = None, account: Optional[str] = None) -> dict:
        return self._s(account).search(q, kind, int(n), order, channel_id)

    def video(self, video: str, account: Optional[str] = None) -> dict:
        return self._s(account).video(api.resolve(video)['id'])

    def channel(self, channel: str, account: Optional[str] = None) -> dict:
        return self._s(account).channel(channel)

    def playlist(self, playlist_id: str, n: int = 50, account: Optional[str] = None) -> dict:
        return self._s(account).playlist(playlist_id, int(n))

    def trending(self, region: str = 'US', n: int = 24, account: Optional[str] = None) -> dict:
        return self._s(account).trending(region, int(n))

    def comments(self, video: str, n: int = 20, account: Optional[str] = None) -> dict:
        return self._s(account).comments(api.resolve(video)['id'], int(n))

    # ── accounts ──
    def set_client(self, client_id: str = '', client_secret: str = '', api_key: str = '',
                   redirect_uri: str = '') -> dict:
        """Store the Google OAuth client (and optional Data API key) in ~/.mod/youtube/client.json."""
        return store.set_client(client_id, client_secret, api_key, redirect_uri)

    def client(self) -> dict:
        return store.client_status()

    def login(self, hint: str = '') -> dict:
        """Start a Google sign-in: returns the consent url to open in a browser. The running
        server (m youtube/serve) receives the callback and stores the account."""
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/auth/start?hint={hint}', timeout=5) as r:
                out = json.loads(r.read())
        except Exception as e:
            return {'error': f'the server must be running for the OAuth callback: m youtube/serve ({e})'}
        out['hint'] = 'open url in a browser, pick the Google account, then m youtube/accounts'
        return out

    def accounts(self) -> dict:
        return {'active': store.active_id(), 'accounts': store.accounts(), 'client': store.client_status()}

    def use(self, account: str) -> dict:
        """Make one signed-in account the active one."""
        return store.set_active(account)

    def logout(self, account: str) -> dict:
        """Revoke at Google and forget the account."""
        return auth.revoke(account)

    # ── as a user ──
    def subscriptions(self, n: int = 50, account: Optional[str] = None) -> dict:
        return self._s(account).subscriptions(int(n))

    def feed(self, n: int = 24, account: Optional[str] = None) -> dict:
        return self._s(account).feed(int(n))

    def playlists(self, account: Optional[str] = None) -> dict:
        return self._s(account).my_playlists()

    def create_playlist(self, title: str, privacy: str = 'private', description: str = '',
                        account: Optional[str] = None) -> dict:
        return self._s(account).create_playlist(title, description, privacy)

    def playlist_add(self, playlist_id: str, video: str, account: Optional[str] = None) -> dict:
        return self._s(account).playlist_add(playlist_id, api.resolve(video)['id'])

    def like(self, video: str, rating: str = 'like', account: Optional[str] = None) -> dict:
        return self._s(account).rate(api.resolve(video)['id'], rating)

    def subscribe(self, channel: str, account: Optional[str] = None) -> dict:
        return self._s(account).subscribe(channel)

    def unsubscribe(self, channel: str, account: Optional[str] = None) -> dict:
        return self._s(account).unsubscribe(channel)

    def comment(self, video: str, text: str, account: Optional[str] = None) -> dict:
        return self._s(account).comment(api.resolve(video)['id'], text)

    # ── as a creator ──
    def me(self, account: Optional[str] = None) -> dict:
        return self._s(account).me()

    def my_videos(self, n: int = 25, account: Optional[str] = None) -> dict:
        return self._s(account).my_videos(int(n))

    def upload(self, path: str, title: str, description: str = '', tags=None, privacy: str = 'private',
               category_id: str = '22', publish_at: Optional[str] = None, account: Optional[str] = None) -> dict:
        """Resumable upload of a local file. Private unless told otherwise."""
        def prog(sent, size):
            print(f'  {sent / size:6.1%}  {sent}/{size}', file=sys.stderr, flush=True)
        return self._s(account).upload(path, title, description, _list(tags), privacy, str(category_id),
                                       publish_at=publish_at, progress=prog)

    def update_video(self, video: str, title: Optional[str] = None, description: Optional[str] = None,
                     tags=None, privacy: Optional[str] = None, account: Optional[str] = None) -> dict:
        return self._s(account).update_video(api.resolve(video)['id'], title, description, _list(tags), privacy)

    def thumbnail(self, video: str, path: str, account: Optional[str] = None) -> dict:
        return self._s(account).set_thumbnail(api.resolve(video)['id'], path)

    def analytics(self, start: str, end: str, metrics: Optional[str] = None, dimensions: str = 'day',
                  account: Optional[str] = None) -> dict:
        kw = {'dimensions': dimensions or None}
        if metrics:
            kw['metrics'] = metrics
        return self._s(account).analytics(start, end, **kw)

    def my_comments(self, n: int = 25, moderation: str = 'published', account: Optional[str] = None) -> dict:
        return self._s(account).my_comments(int(n), moderation)

    def moderate(self, comment_id: str, status: str = 'published', ban: bool = False,
                 account: Optional[str] = None) -> dict:
        return self._s(account).moderate(comment_id, status, bool(ban))

    # ── service ──
    def mcp(self) -> dict:
        """The MCP tools this module publishes, by ring."""
        rings = {}
        for t in mcp_server.tool_list():
            rings.setdefault(t['_meta']['ring'], []).append(t['name'])
        return {'rings': rings, 'http': f'http://localhost:{self.port}/mcp',
                'stdio': f'cd {MODULE_DIR} && python3 -m ytmod.mcp_server'}

    def serve(self, port: Optional[int] = None, host: str = '127.0.0.1') -> dict:
        port = int(port or self.port)
        subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        # cwd = module dir so `ytmod` imports; the server never imports `mod`.
        cmd = ['pm2', 'start', sys.executable, '--name', PM2_NAME, '--cwd', str(MODULE_DIR),
               '--', '-m', 'ytmod.server', '--port', str(port), '--host', host]
        r = subprocess.run(cmd, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'pm2': PM2_NAME, 'app': f'http://{host}:{port}/',
                'mcp': f'http://{host}:{port}/mcp', **({'error': r.stderr[-800:]} if r.returncode else {})}

    def kill(self) -> dict:
        r = subprocess.run(['pm2', 'delete', PM2_NAME], capture_output=True, text=True)
        return {'killed': r.returncode == 0, 'pm2': PM2_NAME}

    def status(self) -> dict:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/health', timeout=2) as r:
                return {'running': True, **json.loads(r.read())}
        except Exception as e:
            return {'running': False, 'error': str(e), 'hint': 'm youtube/serve',
                    'client': store.client_status(), 'accounts': len(store.accounts())}

    def test(self) -> dict:
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q', 'test'], cwd=MODULE_DIR,
                           capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}
