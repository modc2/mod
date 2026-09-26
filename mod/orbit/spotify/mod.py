"""spotify — Spotify as a mod: one adapter, one MCP server, one set of verbs.

Your Spotify account addressable from the CLI, from HTTP, and from any MCP
client, with the same code behind all three:

    m spotify/set_key client_id=… client_secret=…   # once, from the dashboard
    m spotify/login                                  # OAuth (PKCE), browser consent
    m spotify/status                                 # who, what, where (also `forward`)

    m spotify/search "boards of canada roygbiv"
    m spotify/play "roygbiv"                         # phrase, URI or open.spotify link
    m spotify/queue "aphex twin xtal"
    m spotify/now                                    # what is playing, on which device
    m spotify/devices ; m spotify/transfer device="Kitchen"
    m spotify/top type=artists time_range=short_term
    m spotify/playlist_create name="dinner" tracks='["khruangbin","sade smooth operator"]'

    m spotify/serve                                  # REST + console + MCP on :50610
    m spotify/mcp_config                             # drop-in client config

Credentials live off-tree in ~/.mod/spotify/ (0600) and never in config.json.
Playback control needs Spotify Premium; search and library reads do not.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Appended, never prepended: this directory holds mod.py, which would shadow
# the protocol's own `mod` package for anything that imports it after us.
if HERE not in sys.path:
    sys.path.append(HERE)


class Mod:
    description = """
    spotify — an MCP server and adapter for the Spotify Web API over the mod
    protocol. OAuth 2.0 (authorization code + PKCE) with tokens kept off-tree,
    21 MCP tools (search, play, pause, skip, queue, devices, transfer, top,
    recent, library, playlists, raw), a REST mirror and a browser console.
    Every call runs against the caller's own account — no house token.
    """

    def __init__(self, port=None, token=None, **kwargs):
        self.dir = HERE
        cfg = self.config()
        self.port = int(port or os.environ.get('PORT') or cfg.get('port', 50610))
        self.base = cfg.get('base_path', '/spotify')
        self._token = token

    # ── plumbing ─────────────────────────────────────────────────

    def config(self):
        try:
            with open(os.path.join(HERE, 'config.json')) as f:
                return json.load(f)
        except Exception:
            return {}

    @property
    def api(self):
        """The adapter, built per call so a re-login is picked up immediately."""
        from spotify import Spotify
        return Spotify(token=self._token)

    def info(self):
        """What this module is, and every route it serves."""
        import api
        return api.info()

    def forward(self, **kwargs):
        """Default call: auth state, what is playing, which devices are alive."""
        return self.status()

    # ── auth ─────────────────────────────────────────────────────

    def set_key(self, client_id=None, client_secret=None, redirect_uri=None):
        """Store the Spotify app credentials in ~/.mod/spotify/keys.json (0600)."""
        return self.api.set_key(client_id, client_secret, redirect_uri)

    def status(self):
        """Auth state + player state. Never prints a secret."""
        import mcp
        return mcp.call_tool('spotify_status', {})

    def login(self, timeout=180, open_browser=False, scopes=None):
        """OAuth in one step: print the URL, catch the loopback redirect."""
        return self.api.login(timeout=timeout, open_browser=open_browser, scopes=scopes)

    def authorize_url(self, scopes=None, redirect_uri=None, show_dialog=False):
        """Headless flow, step 1: the URL to open in any browser."""
        return self.api.authorize_url(scopes=scopes, redirect_uri=redirect_uri,
                                      show_dialog=show_dialog)

    def exchange(self, code, state=None, redirect_uri=None):
        """Headless flow, step 2: the ?code= from the redirect → tokens."""
        return self.api.exchange(code, state=state, redirect_uri=redirect_uri)

    def refresh(self):
        """Force a token refresh (normally automatic)."""
        return self.api.refresh()

    def logout(self):
        """Forget the user tokens; the app credentials stay."""
        return self.api.logout()

    # ── catalog ──────────────────────────────────────────────────

    def search(self, q, type='track', limit=10, market=None, offset=0):
        """Search the catalog: track, artist, album, playlist, show, episode."""
        return self.api.search(q, type=type, limit=limit, market=market, offset=offset)

    def lookup(self, uri):
        """A spotify: URI / open.spotify.com link / id → the full object."""
        return self.api.lookup(uri)

    def me(self):
        """The signed-in account."""
        return self.api.me()

    # ── player ───────────────────────────────────────────────────

    def now(self):
        """What is playing right now."""
        return self.api.now_playing()

    now_playing = now

    def play(self, query=None, uri=None, device=None, position_ms=None, shuffle=None):
        """Play a phrase, URI or link — or resume, with no argument."""
        return self.api.play(query=query, uri=uri, device=device,
                             position_ms=position_ms, shuffle=shuffle)

    def pause(self, device=None):
        """Pause playback."""
        return self.api.pause(device=device)

    def next(self, device=None):
        """Skip forward."""
        return self.api.next(device=device)

    skip = next

    def previous(self, device=None):
        """Skip back."""
        return self.api.previous(device=device)

    back = previous

    def seek(self, position_ms, device=None):
        """Jump to a position in the current track."""
        return self.api.seek(position_ms, device=device)

    def volume(self, percent, device=None):
        """Set volume 0–100."""
        return self.api.volume(percent, device=device)

    def shuffle(self, state=True, device=None):
        """Shuffle on/off."""
        return self.api.shuffle(state, device=device)

    def repeat(self, state='context', device=None):
        """Repeat track | context | off."""
        return self.api.repeat(state, device=device)

    def devices(self):
        """Every device Spotify can reach, and which one is active."""
        return self.api.devices()

    def transfer(self, device, play=True):
        """Move playback to another device (by name or id)."""
        return self.api.transfer(device, play=play)

    def queue(self, query=None, uri=None, device=None):
        """Add a track to the queue; with no argument, show what is up next."""
        if not (query or uri):
            return self.api.up_next()
        return self.api.queue(query=query, uri=uri, device=device)

    def up_next(self, limit=10):
        """What plays after the current item."""
        return self.api.up_next(limit=limit)

    # ── library ──────────────────────────────────────────────────

    def recent(self, limit=20):
        """Recently played, newest first."""
        return self.api.recent(limit=limit)

    def top(self, type='tracks', time_range='medium_term', limit=20):
        """Most-played tracks or artists over 4 weeks / 6 months / years."""
        return self.api.top(type=type, time_range=time_range, limit=limit)

    def saved(self, limit=20, offset=0):
        """Saved tracks in Your Library."""
        return self.api.saved(limit=limit, offset=offset)

    def save(self, query=None, uri=None, remove=False):
        """Save a track (remove=1 unsaves it)."""
        return self.api.save(query=query, uri=uri, remove=remove)

    # ── playlists ────────────────────────────────────────────────

    def playlists(self, limit=50, offset=0):
        """Your playlists."""
        return self.api.playlists(limit=limit, offset=offset)

    def playlist(self, id, limit=100):
        """One playlist and its tracks."""
        return self.api.playlist(id, limit=limit)

    def playlist_create(self, name, public=False, description=None, tracks=None):
        """Create a playlist, optionally filled with URIs or search phrases."""
        return self.api.playlist_create(name, public=public, description=description,
                                        uris=tracks)

    def playlist_add(self, id, tracks, position=None):
        """Add URIs or search phrases to a playlist."""
        return self.api.playlist_add(id, tracks, position=position)

    def playlist_remove(self, id, tracks):
        """Remove tracks from a playlist."""
        return self.api.playlist_remove(id, tracks)

    def raw(self, path, method='GET', body=None, params=None):
        """Escape hatch: any Spotify Web API endpoint with your token."""
        return self.api.raw(path, method=method, body=body, params=params)

    # ── mcp ──────────────────────────────────────────────────────

    def tools(self):
        """The MCP tool registry this module serves."""
        import mcp
        return {'tools': mcp.tool_list(), 'count': len(mcp.TOOLS)}

    def mcp_call(self, tool, arguments=None, **kwargs):
        """Call one MCP tool in-process — the same path the server takes."""
        import mcp
        return mcp.call_tool(tool, {**(arguments or {}), **kwargs})

    def mcp_config(self):
        """Drop-in client config for Claude Code / Desktop and friends."""
        return {'mcpServers': {
            'spotify': {'command': 'python3', 'args': [os.path.join(HERE, 'mcp.py')]},
            'spotify-http': {'type': 'http', 'url': f'http://localhost:{self.port}/mcp'},
        }}

    # ── serve ────────────────────────────────────────────────────

    def serve(self, port=None, background=True, **kwargs):
        """Run REST + console + MCP on one port, under pm2 as spotify-api."""
        port = int(port or self.port)
        if not background:
            import api
            return api.serve(port)
        self.kill()
        env = {**os.environ, 'PORT': str(port)}
        subprocess.run(['pm2', 'start', sys.executable, '--name', 'spotify-api',
                        '--cwd', HERE, '--', os.path.join(HERE, 'api.py'),
                        '--port', str(port)],
                       cwd=HERE, env=env, capture_output=True)
        return {'api': f'http://localhost:{port}',
                'console': f'http://localhost:{port}{self.base}',
                'mcp': f'http://localhost:{port}/mcp',
                'callback': f'http://127.0.0.1:{port}/callback',
                'process': 'spotify-api'}

    def kill(self, **kwargs):
        """Stop the server."""
        killed = []
        for name in ('spotify-api', 'spotify.api', 'spotify-app'):
            r = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    def test(self, **kwargs):
        """Run the module's tests (offline — no Spotify account needed)."""
        r = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                            os.path.join(HERE, 'test')],
                           cwd=HERE, capture_output=True, text=True)
        return {'ok': r.returncode == 0, 'output': (r.stdout + r.stderr)[-4000:]}

    def readme(self):
        with open(os.path.join(HERE, 'README.md')) as f:
            return f.read()
