"""
crates — a DJ booth, a pattern studio and a crate whose playlists are yours.

A fork of ``orbit/musica``: the same two decks with waveforms, beatmatch,
three-band EQ, filters, beat loops and hot cues; the same step sequencer and
piano roll on the same clock. What this one adds is everything after the find —
**playlists you own, links you hand out, and an MCP server so an agent can do
both for you**.

All the audio work happens client side. Files are decoded in the browser and
mixed through Web Audio, and tempo and key are detected locally — nothing you
drop on a deck is uploaded anywhere. Platform credentials are shared with
musica in ``~/.mod/musica/``; this module's own state is the playlist library
in ``~/.mod/crates/``.

The crate reaches five platforms, and four of them play. Spotify is metadata
and embeds only: its streamed audio is DRM-protected and cannot be routed
through Web Audio, so a Spotify find is for planning a set. Bandcamp,
SoundCloud, YouTube and the Internet Archive all hand over audio their own
players decode, and so can this one — a track from any of them loads straight
onto a deck, gets analysed for tempo and key, and mixes like a file.
``platforms.py`` owns all four keyless adapters; YouTube goes through yt-dlp.

``playlists.py`` owns the kept half. A playlist belongs to whoever proves they
own it — a mod-protocol token (the fleet's shared ``m.mod('auth')`` identity)
or a guest key this module mints for a user with no wallet — and sharing is a
second, separate id that grants reading and copying and never writing. The
console never blocks on sign-in: the key is minted silently the first time
something is kept.

``mcp.py`` exposes the crate AND the library as 19 tools over JSON-RPC
(``POST /mcp``, or stdio), so an agent can search every platform, resolve a
link, build a playlist by phrase and share it with the same calls the console
makes.

This is the anchor file: the orbit loader imports it by path and instantiates
``Mod``. Everything the module exposes to the CLI, the gateway and other
modules is a public method on that class.

CLI:
    m crates                   # null call → info()
    m crates/play              # serve the console and open a browser
    m crates/serve             # run it under pm2, then register the route
    m crates/url               # where it is
    m crates/decks             # the signal chain, deck by deck
    m crates/kit               # the sequencer's voices
    m crates/set_key client_id=… client_secret=…      # Spotify app keys
    m crates/search q="four tet"                          # every platform at once
    m crates/search q="four tet" source=bandcamp kind=album
    m crates/resolve url=https://fourtet.bandcamp.com/album/three
    m crates/stream source=soundcloud id=2176707750      # where the MP3 is
    m crates/search q="roygbiv" source=youtube            # yt-dlp, proxied audio
    m crates/search q="live 1977" source=archive          # the Internet Archive
    m crates/archive_item id=gd1977-05-08.sbd.hicks.4982.sbeok.shnf
    m crates/youtube_playlist id=PLBsm_SagFMmefZzqPX4iD8FxlbQMI5eHs
    m crates/mcp               # the MCP server's tool list
    m crates/guest_key                                    # a key to own playlists with
    m crates/playlist_new name="Friday warmup" guest=…    # keep one
    m crates/playlist_add id=pl_… q="burial archangel" guest=…   # search + append
    m crates/playlist_share id=pl_… listed=true guest=…   # → a link anyone can open
    m crates/playlist_feed                                # what is shared publicly here
    m crates/platforms         # what each platform will and won't do here
    m crates/test              # files + JS syntax + engine smoke test
    m crates/kill              # stop it
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

import mod as m

MODULE_DIR = Path(__file__).parent

# The orbit loader imports this file by path, so the module directory is not
# necessarily importable. Put it on the path before reaching for our package.
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))


def _platforms():
    """Load ``platforms.py`` by path — same reason as :func:`_spotify`."""
    if '_platforms_mod' not in globals():
        spec = importlib.util.spec_from_file_location(
            'crates_platforms', str(MODULE_DIR / 'platforms.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        globals()['_platforms_mod'] = module
    return globals()['_platforms_mod']


def _mcp():
    """Load ``mcp.py`` by path — it imports platforms.py the same way."""
    if '_mcp_mod' not in globals():
        spec = importlib.util.spec_from_file_location(
            'crates_mcp', str(MODULE_DIR / 'mcp.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        globals()['_mcp_mod'] = module
    return globals()['_mcp_mod']


def _playlists():
    """Load ``playlists.py`` by path — the owned-and-shared half of the crate."""
    if '_playlists_mod' not in globals():
        spec = importlib.util.spec_from_file_location(
            'crates_playlists', str(MODULE_DIR / 'playlists.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        globals()['_playlists_mod'] = module
    return globals()['_playlists_mod']


def _spotify():
    """Load ``spotify.py`` by path.

    Importing it by name would go through sys.path, where this module's own
    directory sits next to a framework package also called ``mod`` — loading by
    path keeps that collision out of the picture.
    """
    if '_spotify_mod' not in globals():
        spec = importlib.util.spec_from_file_location(
            'crates_spotify', str(MODULE_DIR / 'spotify.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        globals()['_spotify_mod'] = module
    return globals()['_spotify_mod']


SOURCES = ('spotify', 'bandcamp', 'soundcloud', 'youtube', 'archive')

# Sources whose audio a deck can actually decode. Spotify is the odd one out
# and every "can this play" check in the module and the console reads this.
PLAYABLE = ('bandcamp', 'soundcloud', 'youtube', 'archive')


class Mod:
    description = ('A DJ booth and an FL-style pattern studio in one browser '
                   'tab — two decks, a mixer, a sequencer, and a crate that '
                   'searches Spotify, Bandcamp, SoundCloud, YouTube and the '
                   'Internet Archive at once. Everything but Spotify loads '
                   'straight onto a deck. Also an MCP server for agents.')

    # What the HTTP API exposes. Every public method is a function of this
    # module, but the API answers from the public gateway, so it serves only the
    # ones that read: serve/kill/set_key and friends stay on the CLI.
    API_FNS = ('info', 'health', 'readme', 'url', 'path', 'files', 'decks',
               'kit', 'keys', 'search', 'track', 'playlist', 'album', 'artist',
               'my_playlists', 'spotify_status', 'platforms', 'resolve',
               'stream', 'discover', 'bandcamp_page', 'soundcloud_playlist',
               'soundcloud_user', 'youtube_video', 'youtube_playlist',
               'youtube_channel', 'archive_item', 'archive_collection',
               'tools', 'mcp',
               # the owned half: these read or write ONE caller's playlists,
               # and every one of them decides whose from the token or guest
               # key it is handed — never from the request being local.
               'whoami', 'guest_key', 'playlists', 'playlist_open',
               'playlist_new', 'playlist_edit', 'playlist_delete',
               'playlist_add', 'playlist_remove', 'playlist_move',
               'playlist_set', 'playlist_reorder',
               'playlist_share', 'playlist_copy', 'playlist_feed')

    # The mixer's signal chain, in order. The console builds exactly this graph
    # per deck; keeping the description here means `m crates/decks` and the code
    # can't drift apart silently.
    CHAIN = [
        ('source', 'AudioBufferSourceNode — playbackRate carries pitch and tempo'),
        ('trim', 'input gain, set once when the track loads'),
        ('eq.low', 'lowshelf 120Hz, −∞…+6dB — full cut kills the kick'),
        ('eq.mid', 'peaking 1kHz Q=0.9, −∞…+6dB'),
        ('eq.high', 'highshelf 6kHz, −∞…+6dB'),
        ('filter', 'one bipolar knob: left is a lowpass sweep, right a highpass'),
        ('fader', 'the channel fader'),
        ('cross', 'crossfader leg — constant-power curve'),
        ('master', 'summed with the sequencer, then limited'),
    ]

    # Voices the sequencer synthesises. No samples on disk: every one of these
    # is oscillators and filtered noise, so the module ships without audio
    # assets and a channel can still be pointed at a file you drop on it.
    KIT = [
        ('kick', 'sine with a pitch drop, 110→45Hz'),
        ('snare', 'noise burst through a bandpass plus a 190Hz body'),
        ('clap', 'three noise bursts 9ms apart into a longer tail'),
        ('hat', 'six detuned squares through a highpass — closed or open'),
        ('tom', 'tuned sine drop'),
        ('rim', 'short bandpassed click at 1.7kHz'),
        ('cowbell', 'two squares at 540 and 800Hz'),
        ('bass', 'subtractive synth voice on the piano roll — saw/square into a '
                 'resonant lowpass with its own envelope'),
        ('lead', 'the same voice, brighter and polyphonic'),
        ('sampler', 'any file you drop on the channel, pitched by the roll'),
    ]

    def __init__(self, key='crates', network='testnet'):
        self.key = m.key(key)
        self.network = network
        self.module_dir = MODULE_DIR
        cfg = self._config()
        self.port = int(cfg.get('port', 50790))
        self.app_port = int(cfg.get('app_port', 50790))

    def _config(self) -> dict:
        try:
            with (MODULE_DIR / 'config.json').open() as fh:
                return json.load(fh)
        except Exception:
            return {}

    # ── module surface ───────────────────────────────────────────────────

    def forward(self, action=None, **kwargs):
        """CLI entry: ``m crates <action> [args]``."""
        if action is None:
            return self.info()
        fn = getattr(self, str(action), None)
        if not callable(fn) or str(action).startswith('_'):
            return {'error': f'unknown action {action!r}', 'fns': self._fns()}
        return fn(**kwargs)

    def _fns(self) -> List[str]:
        return [k for k in dir(self)
                if not k.startswith('_') and callable(getattr(self, k))]

    def info(self) -> dict:
        cfg = self._config()
        return {
            'name': 'crates',
            'title': 'CRATES — decks, mixer, sequencer',
            'description': self.description,
            'version': cfg.get('version', '0.1.0'),
            'network': self.network,
            'app': self.url(),
            'api': self.api_url(),
            'schema': cfg.get('schema'),
            'surfaces': {
                'DECKS': 'two players, waveform + beat grid, sync, loops, hot cues',
                'MIXER': 'EQ, filter, crossfader, master limiter, record the mix',
                'STUDIO': 'step sequencer + piano roll on the master clock',
                'CRATE': 'Spotify + Bandcamp + SoundCloud search, paste-a-link, '
                         'a set list with key matching, and whatever files you drop in',
            },
            'audio': 'decoded and mixed in your browser — no upload, no server DSP',
            'platforms': {k: v.get('configured') for k, v in self.platforms().items()
                          if isinstance(v, dict)},
            'fns': self._fns(),
        }

    def health(self) -> dict:
        return {'ok': True, 'module': 'crates'}

    def readme(self) -> str:
        path = self.module_dir / 'README.md'
        return path.read_text() if path.exists() else ''

    def url(self, gateway=None) -> str:
        """Where the console lives."""
        base = self._config().get('base_path', '/crates')
        if gateway:
            return f'{str(gateway).rstrip("/")}{base}'
        return f'http://localhost:{self.app_port}{base}'

    def api_url(self, gateway=None) -> str:
        """Where the module's functions answer: ``/api/crates``."""
        if gateway:
            return f'{str(gateway).rstrip("/")}/api/crates'
        return f'http://localhost:{self.port}'

    def path(self) -> str:
        """The directory the console is served from."""
        return str(self.module_dir / 'web')

    def files(self) -> dict:
        """Every file that makes up the console, with its size."""
        web = self.module_dir / 'web'
        return {str(p.relative_to(web)): p.stat().st_size
                for p in sorted(web.rglob('*')) if p.is_file()}

    def decks(self) -> dict:
        """The mixer's signal chain, deck by deck."""
        return {
            'decks': ['A', 'B'],
            'chain': [{'stage': s, 'about': a} for s, a in self.CHAIN],
            'sync': "playbackRate matches the other deck's BPM, then the "
                    'transport is nudged so downbeats land together',
            'loops': [0.25, 0.5, 1, 2, 4, 8, 16],
            'cues': 4,
            'cue_monitor': 'pre-fader solo into the same output — a real '
                           'headphone cue needs a second device, which the '
                           'browser only offers behind setSinkId',
            'record': 'MediaRecorder off the master bus → .webm',
        }

    def kit(self) -> dict:
        """The synthesised voices the sequencer ships with."""
        return {
            'voices': [{'name': n, 'about': a} for n, a in self.KIT],
            'samples_on_disk': 0,
            'note': 'every voice is synthesised at play time; drop a file on a '
                    'channel to make it a sampler instead',
            'steps': [16, 32, 64],
            'swing': '0–75% on 16ths',
            'patterns': 8,
        }

    # ── the crate: three platforms ───────────────────────────────────────

    def keys(self) -> dict:
        """Credential status for every platform — masked, never a secret."""
        return self.platforms()

    def spotify_status(self) -> dict:
        """Whether Spotify is wired up, and what it will and won't answer."""
        return _spotify().status()

    def platforms(self) -> dict:
        """What each platform will and will not do from this deployment."""
        pf = _platforms()
        return {
            'spotify': _spotify().status(),
            'bandcamp': pf.bc_status(),
            'soundcloud': pf.sc_status(),
            'youtube': pf.yt_status(),
            'archive': pf.ia_status(),
            'playable': list(PLAYABLE),
            'streams': 'Bandcamp, SoundCloud, YouTube and Internet Archive '
                       'tracks decode into a deck; Spotify is DRM-protected '
                       'and stays metadata + embeds',
        }

    def set_key(self, client_id=None, client_secret=None, soundcloud_client_id=None, **_) -> dict:
        """Store platform credentials in ``~/.mod/crates/keys.json``.

        Spotify: register an app at https://developer.spotify.com/dashboard —
        the client-credentials grant used here needs no redirect URI. If
        orbit/spotify already holds keys, crates reads those and none are
        needed here. SoundCloud needs nothing; ``soundcloud_client_id`` only
        pins one if the scraped web-player id ever stops working.
        """
        pf = _platforms()
        if soundcloud_client_id:
            keys = pf._keys()
            keys['soundcloud_client_id'] = str(soundcloud_client_id).strip()
            pf._write(pf.KEYS_FILE, keys)
            if not (client_id or client_secret):
                return self.platforms()
        if not client_id or not client_secret:
            return {'error': 'client_id and client_secret are both required for Spotify',
                    'usage': 'm crates/set_key client_id=… client_secret=…'}
        _spotify().save_key(client_id, client_secret)
        return self.platforms()

    def _one(self, source, q, kind, limit):
        pf, sp = _platforms(), _spotify()
        try:
            if source == 'spotify':
                return sp.search(q, kind='track' if kind == 'all' else kind, limit=limit)
            if source == 'bandcamp':
                return pf.bc_search(q, kind=kind, limit=limit)
            if source == 'soundcloud':
                return pf.sc_search(q, kind='track' if kind == 'all' else kind, limit=limit)
            if source == 'youtube':
                return pf.yt_search(q, kind=kind, limit=limit)
            if source == 'archive':
                # archive.org indexes items, not tracks: a search for a track
                # kind still answers with the albums that contain them.
                return pf.ia_search(q, limit=limit)
        except (sp.SpotifyError, pf.PlatformError, Exception) as e:  # noqa: BLE001
            return {'source': source, 'error': str(e), 'count': 0, 'items': []}
        return {'source': source, 'error': f'unknown source {source!r}', 'items': []}

    def search(self, q=None, source='all', kind='track', limit=20, offset=0, **_) -> dict:
        """Search one platform or all three at once.

        ``source`` is spotify, bandcamp, soundcloud or all; ``kind`` is track,
        album, artist or playlist (Bandcamp also takes ``all``). With
        source=all the three run in parallel and each reports its own error,
        so one platform being down never empties the crate.
        """
        if not q:
            return {'error': 'q is required', 'usage': 'm crates/search q="four tet"'}
        pf = _platforms()
        link = pf.detect(q)
        if link:
            return self.resolve(q)
        source = str(source or 'all').lower()
        kind = str(kind or 'track').lower()
        limit = int(limit)
        if source != 'all':
            if source not in SOURCES:
                return {'error': f'source must be one of all, {", ".join(SOURCES)}'}
            out = self._one(source, q, kind, limit)
            return out if 'error' not in out else {**out, 'query': q, 'kind': kind}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=len(SOURCES)) as ex:
            results = dict(zip(SOURCES, ex.map(
                lambda src: self._one(src, q, kind, limit), SOURCES)))
        items = []
        # Interleave so the first screen is a mix, not one platform's page.
        lists = [list(results[src].get('items') or []) for src in SOURCES]
        while any(lists):
            for lst in lists:
                if lst:
                    items.append(lst.pop(0))
        return {
            'query': q, 'kind': kind, 'count': len(items), 'items': items,
            'sources': {src: {k: v for k, v in results[src].items() if k != 'items'}
                        for src in SOURCES},
        }

    def resolve(self, url=None, **_) -> dict:
        """A pasted link → the track, album, playlist or artist it points at.

        Spotify, Bandcamp and SoundCloud URLs (and spotify: URIs) are all
        understood. Albums and playlists come back with their tracks.
        """
        pf, sp = _platforms(), _spotify()
        link = pf.detect(url or '')
        if not link:
            return {'error': 'not a Spotify, Bandcamp, SoundCloud, YouTube or '
                             'archive.org link', 'url': url}
        try:
            src, kind, ident = link['source'], link['kind'], link['id']
            if src == 'bandcamp':
                if kind == 'artist':
                    return {**link, 'error': 'a Bandcamp artist page — search their name instead'}
                return pf.bc_page(ident)
            if src == 'soundcloud':
                return pf.sc_resolve(ident)
            if src == 'youtube':
                if kind == 'playlist':
                    return pf.yt_playlist(ident)
                if kind == 'artist':
                    return pf.yt_channel(ident)
                return pf.yt_video(ident)
            if src == 'archive':
                return pf.ia_item(ident.split('/')[0])
            if kind == 'track':
                return sp.track(ident)
            if kind == 'album':
                return sp.album(ident)
            if kind == 'artist':
                return sp.artist_top(ident)
            return sp.playlist(ident, limit=100)
        except (sp.SpotifyError, pf.PlatformError) as e:
            return {**link, 'error': str(e)}

    def stream(self, source=None, id=None, track=None, **_) -> dict:
        """Where a track's audio actually is.

        SoundCloud and the Internet Archive answer with a URL the browser
        fetches itself (``direct: true``). Bandcamp's and YouTube's carry no
        CORS header, so the console pulls those through this module's
        ``/stream/<source>?…`` proxy instead (``direct: false``). Spotify has
        no stream to give.

        ids by source: soundcloud a numeric track id, bandcamp a track or
        album page URL (plus ``track``), youtube a video id or watch URL,
        archive ``identifier/filename``.
        """
        pf = _platforms()
        try:
            if source == 'soundcloud':
                return pf.sc_stream(id)
            if source == 'bandcamp':
                return pf.bc_stream(id, track_id=track)
            if source == 'youtube':
                return pf.yt_stream(id)
            if source == 'archive':
                return pf.ia_stream(id)
        except pf.PlatformError as e:
            return {'error': str(e), 'source': source, 'id': id}
        if source == 'spotify':
            return {'error': 'Spotify audio is DRM-protected and cannot be decoded — '
                             'preview it in the embed, then load the file or find it '
                             'on Bandcamp, SoundCloud, YouTube or the Internet '
                             'Archive', 'source': source, 'id': id}
        return {'error': f'source must be one of {", ".join(PLAYABLE)}'}

    def discover(self, tag='electronic', slice='top', size=24, **_) -> dict:
        """Bandcamp's discover feed for one tag — top, new or rec."""
        pf = _platforms()
        try:
            return pf.bc_discover(tag, slice_=slice, size=size)
        except pf.PlatformError as e:
            return {'error': str(e), 'tag': tag}

    def bandcamp_page(self, url=None, **_) -> dict:
        """One Bandcamp album or track page, every track listed."""
        if not url:
            return {'error': 'url is required'}
        pf = _platforms()
        try:
            return pf.bc_page(url)
        except pf.PlatformError as e:
            return {'error': str(e), 'url': url}

    def soundcloud_playlist(self, id=None, limit=200, **_) -> dict:
        """A SoundCloud playlist or album with its tracks hydrated."""
        if not id:
            return {'error': 'id is required'}
        pf = _platforms()
        try:
            return pf.sc_playlist(id, limit=int(limit))
        except (pf.PlatformError, ValueError) as e:
            return {'error': str(e), 'id': id}

    def soundcloud_user(self, id=None, limit=50, **_) -> dict:
        """A SoundCloud user's own uploads."""
        if not id:
            return {'error': 'id is required'}
        pf = _platforms()
        try:
            return pf.sc_user_tracks(id, limit=int(limit))
        except (pf.PlatformError, ValueError) as e:
            return {'error': str(e), 'id': id}

    def youtube_video(self, id=None, **_) -> dict:
        """One YouTube video's metadata — an id, or any watch/shorts/youtu.be URL."""
        if not id:
            return {'error': 'id is required', 'usage': 'm crates/youtube_video id=SM4tQcUt_mQ'}
        pf = _platforms()
        try:
            return pf.yt_video(id)
        except pf.PlatformError as e:
            return {'error': str(e), 'id': id}

    def youtube_playlist(self, id=None, limit=100, **_) -> dict:
        """A YouTube playlist, mix or album, with its videos listed."""
        if not id:
            return {'error': 'id is required — a list= id or a playlist URL'}
        pf = _platforms()
        try:
            return pf.yt_playlist(id, limit=int(limit))
        except (pf.PlatformError, ValueError) as e:
            return {'error': str(e), 'id': id}

    def youtube_channel(self, id=None, limit=30, **_) -> dict:
        """A YouTube channel's uploads — @handle, UC… id, or channel URL."""
        if not id:
            return {'error': 'id is required — @handle, a UC… id or a channel URL'}
        pf = _platforms()
        try:
            return pf.yt_channel(id, limit=int(limit))
        except (pf.PlatformError, ValueError) as e:
            return {'error': str(e), 'id': id}

    def archive_item(self, id=None, **_) -> dict:
        """One archive.org item — album, concert or show — with its tracks.

        Each track's id is ``identifier/filename``, which is what
        ``stream(source=archive, id=…)`` takes.
        """
        if not id:
            return {'error': 'id is required — an archive.org identifier'}
        pf = _platforms()
        try:
            return pf.ia_item(id)
        except pf.PlatformError as e:
            return {'error': str(e), 'id': id}

    def archive_collection(self, id='netlabels', q='', limit=30, **_) -> dict:
        """One corner of the archive: ``etree`` (Live Music Archive),
        ``netlabels``, ``78rpm``, ``audio_music``… optionally filtered by ``q``.
        """
        pf = _platforms()
        try:
            out = pf.ia_search(q or '*', limit=int(limit), collection=id)
        except (pf.PlatformError, ValueError) as e:
            return {'error': str(e), 'collection': id}
        out['collection'] = id
        return out

    def tools(self, **_) -> dict:
        """The MCP tool list — name, one-line summary and arguments for each."""
        mcp = _mcp()
        return {'server': 'crates', 'transport': {
            'http': self.api_url().rstrip('/') + '/mcp',
            'stdio': f'python3 {MODULE_DIR / "mcp.py"}'},
            'count': len(mcp.TOOLS),
            'tools': [{'name': t['name'],
                       'summary': t['description'].split('. ')[0] + '.',
                       'args': sorted((t['inputSchema'].get('properties') or {}).keys()),
                       'required': t['inputSchema'].get('required', [])}
                      for t in mcp.tool_list()]}

    def mcp(self, method=None, name=None, arguments=None, **kwargs) -> dict:
        """Call the MCP server without speaking JSON-RPC.

        ``m crates/mcp`` lists the tools; ``m crates/mcp name=crates_search
        arguments='{"q":"four tet"}'`` runs one. The HTTP transport at
        ``/mcp`` is the real one — this is the same handlers from the CLI.
        """
        mcp = _mcp()
        if method and method not in ('tools/call', 'tools/list'):
            return {'error': f'method must be tools/list or tools/call, not {method!r}'}
        if not name:
            return self.tools()
        args = arguments
        if isinstance(args, str):
            try:
                args = json.loads(args or '{}')
            except json.JSONDecodeError as e:
                return {'error': f'arguments is not JSON: {e}'}
        args = dict(args or {})
        args.update({k: v for k, v in kwargs.items() if not k.startswith('_')})
        try:
            return {'tool': name, 'result': mcp.call_tool(name, args)}
        except Exception as e:                                  # noqa: BLE001
            return {'tool': name, 'error': f'{type(e).__name__}: {e}'}

    # ── playlists: yours, and the ones you hand out ──────────────────────
    #
    # Every function below takes the caller's credential explicitly — `token`
    # (a mod-protocol token, the fleet's shared identity) or `guest` (the
    # random key the console keeps in localStorage). serve.py lifts both off
    # the request headers, mcp.py off the tool arguments, and the CLI takes
    # them as plain arguments; none of the three can be spoofed by being
    # nearby, because the owner id is derived from the credential and nothing
    # else. Errors come back as {'error': …} rather than tracebacks, since the
    # console prints whatever it is given.

    GATEWAY = 'https://modc2.com'

    def _pl(self, fn, *a, **kw):
        pl = _playlists()
        try:
            return fn(pl, *a, **kw)
        except pl.PlaylistError as e:
            return {'error': str(e)}

    def share_url(self, share_id, gateway=None) -> str:
        """The link that opens a shared playlist in someone else's console."""
        base = str(gateway or self.GATEWAY).rstrip('/')
        return f'{base}{self._config().get("base_path", "/crates")}?p={share_id}'

    def whoami(self, token=None, guest=None, **_) -> dict:
        """Who this credential makes you, and what that lets you do."""
        pl = _playlists()
        try:
            me = pl.who(token, guest)
        except pl.PlaylistError as e:
            return {'error': str(e), 'anon': True}
        return {**me,
                'can': ['read shared playlists', 'browse the directory'] if me['anon']
                else ['keep playlists', 'share them', 'copy other people\'s'],
                'note': ('anonymous — pass a mod token, or ask for a guest key '
                         'with guest_key() and keep it' if me['anon'] else
                         'a wallet follows you everywhere; a guest key is only '
                         'as portable as the browser holding it')}

    def guest_key(self, **_) -> dict:
        """Mint a guest key for a caller with no wallet.

        Nothing is stored here: the key IS the identity, hashed into an owner
        id the first time it is used. Whoever holds it owns those playlists,
        so it is worth keeping and worth not pasting anywhere public.
        """
        key = _playlists().new_guest_key()
        return {'guest': key, 'owner': _playlists().who(guest=key)['id'],
                'keep_it': 'this is a password, not a username — the console '
                           'stores it in this browser and nowhere else'}

    def playlists(self, token=None, guest=None, **_) -> dict:
        """Every playlist you own, newest change first."""
        return self._pl(lambda pl: pl.mine(token, guest))

    def playlist_open(self, id=None, share=None, token=None, guest=None, **_) -> dict:
        """One playlist in full — yours by id, anyone's by share id."""
        out = self._pl(lambda pl: pl.open_(id=id, share=share, token=token,
                                           guest=guest))
        if isinstance(out, dict) and out.get('share_id'):
            out['url'] = self.share_url(out['share_id'])
        return out

    def playlist_new(self, name=None, note='', tracks=None, token=None,
                     guest=None, **_) -> dict:
        """Start a playlist, optionally with tracks already in it."""
        return self._pl(lambda pl: pl.create(name=name, note=note, tracks=tracks,
                                             token=token, guest=guest))

    def playlist_edit(self, id=None, name=None, note=None, token=None,
                      guest=None, **_) -> dict:
        """Rename one, or change its note."""
        return self._pl(lambda pl: pl.edit(id=id, name=name, note=note,
                                           token=token, guest=guest))

    def playlist_delete(self, id=None, token=None, guest=None, **_) -> dict:
        """Delete one of yours, share link and all."""
        return self._pl(lambda pl: pl.delete(id=id, token=token, guest=guest))

    def playlist_add(self, id=None, track=None, tracks=None, q=None, url=None,
                     source=None, at=None, token=None, guest=None, **_) -> dict:
        """Add to a playlist — a track object, a link, or just a search.

        The last form is what an agent actually wants: ``playlist_add
        id=pl_… q="four tet baby"`` searches the crate, takes the best hit
        that can actually be played, and appends it. ``source`` narrows the
        search when it matters ("the Bandcamp one").
        """
        if track is None and tracks is None:
            found = self._find_one(q=q, url=url, source=source)
            if 'error' in found:
                return found
            track = found['track']
        out = self._pl(lambda pl: pl.add(id=id, track=track, tracks=tracks,
                                         at=at, token=token, guest=guest))
        return out

    def _find_one(self, q=None, url=None, source=None) -> dict:
        """The one track a link or a search phrase means, ready to add.

        Playable sources win: a Spotify hit is a nice piece of metadata, but a
        playlist row that can be dropped on a deck is worth more than one that
        can only be read, so a streamable match is preferred over an exact
        earlier one.
        """
        if url:
            res = self.resolve(url)
            if res.get('error'):
                return res
            if res.get('kind') in (None, 'track') and res.get('id'):
                return {'track': res}
            items = res.get('items') or []
            if not items:
                return {'error': f'nothing playable behind {url}'}
            return {'track': items[0]}
        if not q:
            return {'error': 'pass track, tracks, q or url'}
        res = self.search(q, source=source or 'all', kind='track', limit=10)
        if res.get('error'):
            return res
        items = res.get('items') or []
        if not items:
            return {'error': f'no track matches {q!r}'}
        playable = [x for x in items
                    if x.get('source') in PLAYABLE and x.get('streamable') is not False]
        return {'track': (playable or items)[0]}

    def playlist_set(self, id=None, tracks=None, token=None, guest=None, **_) -> dict:
        """Replace the whole track list — the console's auto-save writes here."""
        return self._pl(lambda pl: pl.replace(id=id, tracks=tracks, token=token,
                                              guest=guest))

    def playlist_reorder(self, id=None, keys=None, token=None, guest=None, **_) -> dict:
        """Reorder the tracks already in a playlist, by their keys.

        Safe by construction: keys this playlist does not have are ignored and
        tracks you leave out keep their order at the end, so a reorder can
        never lose a track.
        """
        return self._pl(lambda pl: pl.reorder(id=id, keys=keys, token=token,
                                              guest=guest))

    def playlist_remove(self, id=None, key=None, index=None, token=None,
                        guest=None, **_) -> dict:
        """Take a track out, by its key or its position."""
        return self._pl(lambda pl: pl.remove(id=id, key=key, index=index,
                                             token=token, guest=guest))

    def playlist_move(self, id=None, to=None, token=None, guest=None, **kw) -> dict:
        """Reorder: ``from`` (0-based) moves to ``to``."""
        src = kw.get('from', kw.get('from_', kw.get('index')))
        return self._pl(lambda pl: pl.move(id=id, token=token, guest=guest,
                                           **{'from': src, 'to': to}))

    def playlist_share(self, id=None, on=True, listed=False, token=None,
                       guest=None, **_) -> dict:
        """Mint a share link — or revoke it with ``on=false``.

        The link is read-only for everyone but you. ``listed=true`` also puts
        it in this deployment's public directory (``playlist_feed``); without
        it, the link is the only way in.
        """
        out = self._pl(lambda pl: pl.share(id=id, on=on, listed=listed,
                                           token=token, guest=guest))
        if isinstance(out, dict) and out.get('share_id'):
            out['url'] = self.share_url(out['share_id'])
            out['note'] = ('anyone with this link can play and copy it; only '
                           'you can change it')
        return out

    def playlist_copy(self, share=None, name=None, token=None, guest=None, **_) -> dict:
        """Copy a shared playlist into your own library."""
        return self._pl(lambda pl: pl.copy(share=share, name=name, token=token,
                                           guest=guest))

    def playlist_feed(self, limit=30, **_) -> dict:
        """Playlists people on this deployment have shared publicly."""
        out = self._pl(lambda pl: pl.feed(limit=limit))
        for card in (out.get('items') or []):
            card['url'] = self.share_url(card['share_id'])
        return out

    def warm(self, force=False, **_) -> dict:
        """Clear Bandcamp's JavaScript challenge in a headless browser (CLI only)."""
        return _platforms().bc_warm(force=bool(force))

    def track(self, id=None, **_) -> dict:
        """One Spotify track's metadata."""
        if not id:
            return {'error': 'id is required'}
        sp = _spotify()
        try:
            return sp.track(id)
        except sp.SpotifyError as e:
            return {'error': str(e)}

    def album(self, id=None, **_) -> dict:
        """A Spotify album with its tracks."""
        if not id:
            return {'error': 'id is required'}
        sp = _spotify()
        try:
            return sp.album(id)
        except sp.SpotifyError as e:
            return {'error': str(e)}

    def artist(self, id=None, **_) -> dict:
        """A Spotify artist's top tracks."""
        if not id:
            return {'error': 'id is required'}
        sp = _spotify()
        try:
            return sp.artist_top(id)
        except sp.SpotifyError as e:
            return {'error': str(e)}

    def playlist(self, id=None, limit=50, **_) -> dict:
        """A Spotify playlist's tracks — public, or your own if orbit/spotify is logged in."""
        if not id:
            return {'error': 'id is required'}
        sp = _spotify()
        try:
            return sp.playlist(id, limit=limit)
        except sp.SpotifyError as e:
            return {'error': str(e)}

    def my_playlists(self, limit=50, **_) -> dict:
        """Your own Spotify playlists, via orbit/spotify's login."""
        sp = _spotify()
        try:
            return sp.my_playlists(limit=limit)
        except sp.SpotifyError as e:
            return {'error': str(e), 'items': [], 'count': 0}

    # ── serve / register ─────────────────────────────────────────────────

    def _pm2_start(self, name, cmd, cwd=None, env=None) -> bool:
        subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
        pm2_cmd = ['pm2', 'start', cmd[0], '--name', name]
        if cwd:
            pm2_cmd += ['--cwd', cwd]
        pm2_cmd += ['--'] + list(cmd[1:])
        r = subprocess.run(pm2_cmd, capture_output=True, text=True,
                           env={**os.environ, **(env or {})})
        if r.returncode != 0:
            print(r.stderr[-800:])
        return r.returncode == 0

    def _pm2_kill(self, name) -> bool:
        return subprocess.run(['pm2', 'delete', name],
                              capture_output=True, text=True).returncode == 0

    def serve_app(self, app_port=None) -> dict:
        """Run the console's server under pm2."""
        app_port = int(app_port or self.app_port)
        script = self.module_dir / 'serve.py'
        if not script.exists():
            return {'error': f'{script} not found'}
        env = {'PORT': str(app_port), 'HOST': '0.0.0.0'}
        cmd = ['python3', str(script), '--port', str(app_port), '--host', '0.0.0.0']
        ok = self._pm2_start('crates.app', cmd, cwd=str(self.module_dir), env=env)
        return {'app': f'http://localhost:{app_port}', 'pm2': 'crates.app', 'ok': ok}

    # The mixing is all client side and the Spotify calls are stateless, so one
    # process answers both halves of the route from the same port.
    def serve_api(self, **_) -> dict:
        return {'ok': True, 'api': self.api_url(), 'pm2': 'crates.app',
                'fns': list(self.API_FNS),
                'note': 'served by crates.app — the console needs no second process'}

    def serve(self, app_port=None, register=True, **_) -> dict:
        """Start the console under pm2, then register the gateway route."""
        out = {'app': self.serve_app(app_port=app_port)}
        if register:
            out['registration'] = self.register()
        out['url'] = self.url()
        return out

    def play(self, port=None, open=True, background=True) -> str:
        """Serve the console in this process and open it in a browser.

        Handy for a quick session; ``serve()`` is what you want for the fleet,
        since that one survives the shell exiting.
        """
        import threading
        import webbrowser
        from http.server import ThreadingHTTPServer

        # Load serve.py by path: `serve` is a common enough name that importing
        # it normally would fight with whatever else is in sys.modules.
        spec = importlib.util.spec_from_file_location(
            'crates_serve', str(self.module_dir / 'serve.py'))
        _serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_serve)

        port = int(port or self.app_port)
        srv = None
        for p in range(port, port + 20):
            try:
                srv = ThreadingHTTPServer(('0.0.0.0', p), _serve.Handler)
                port = p
                break
            except OSError:
                continue
        if srv is None:
            raise OSError(f'no free port in {port}-{port + 20}')

        url = f'http://localhost:{port}/crates/'
        print(f'crates serving at {url}')
        if open:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        if background:
            self._srv = srv
            threading.Thread(target=srv.serve_forever, daemon=True).start()
        else:
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                srv.shutdown()
        return url

    def stop(self) -> dict:
        """Stop a server started by ``play()`` in this process."""
        srv = getattr(self, '_srv', None)
        if srv is None:
            return {'ok': False, 'error': 'nothing running in this process'}
        srv.shutdown()
        srv.server_close()
        self._srv = None
        return {'ok': True}

    def kill(self) -> dict:
        killed = [n for n in ('crates.api', 'crates.app') if self._pm2_kill(n)]
        return {'killed': killed}

    def test(self) -> dict:
        """Smoke test: the console's files exist, parse, and the engine runs.

        The engine harness drives the sequencer's clock, the tempo detector and
        the key detector under node against synthetic audio, so a broken beat
        grid is caught here rather than on a silent deck.
        """
        web = self.module_dir / 'web'
        wanted = ['index.html', 'css/app.css', 'js/engine.js', 'js/analyze.js',
                  'js/synth.js', 'js/sequencer.js', 'js/ui.js', 'js/crate.js',
                  'js/playlists.js', 'js/app.js']
        missing = [f for f in wanted if not (web / f).exists()]
        if missing:
            return {'ok': False, 'missing': missing}

        checks = {'files': True}
        try:
            pf = _platforms()
            links = {
                'https://fourtet.bandcamp.com/album/three': ('bandcamp', 'album'),
                'https://soundcloud.com/four-tet/lost-village-23rd-august-2025': ('soundcloud', 'track'),
                'https://soundcloud.com/clutchrecs/sets/tech-house': ('soundcloud', 'playlist'),
                'https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=x': ('spotify', 'track'),
                'spotify:album:1ATL5GLyefJaxhQzSPVrLX': ('spotify', 'album'),
                'four tet': None,
            }
            bad = [u for u, want in links.items()
                   if (lambda d: (d['source'], d['kind']) if d else None)(pf.detect(u)) != want]
            checks['links'] = 'ok' if not bad else f'misdetected: {bad}'
        except Exception as e:                    # noqa: BLE001
            checks['links'] = f'{type(e).__name__}: {e}'
        checks['playlists'] = self._test_playlists()
        node = subprocess.run(['bash', '-lc', 'command -v node'],
                              capture_output=True, text=True)
        if node.returncode != 0:
            checks['node'] = 'not installed — skipped syntax + engine checks'
            return {'ok': True, 'checks': checks}

        for f in wanted:
            if not f.endswith('.js'):
                continue
            s = subprocess.run(['node', '--check', str(web / f)],
                               capture_output=True, text=True)
            checks[f] = 'ok' if s.returncode == 0 else s.stderr[-400:]

        harness = self.module_dir / 'tests' / 'engine.mjs'
        if harness.exists():
            r = subprocess.run(['node', str(harness)], capture_output=True,
                               text=True, cwd=str(self.module_dir))
            if r.returncode != 0:
                checks['engine'] = (r.stdout[-800:] + r.stderr[-400:]).strip()
            else:
                try:
                    checks['engine'] = json.loads(r.stdout.strip().splitlines()[-1])
                except (json.JSONDecodeError, IndexError):
                    checks['engine'] = r.stdout[-400:]

        ok = (all(v == 'ok' for k, v in checks.items() if k.startswith('js/'))
              and checks.get('links') == 'ok'
              and checks.get('playlists') == 'ok'
              and not isinstance(checks.get('engine'), str))
        return {'ok': ok, 'checks': checks}

    def _test_playlists(self):
        """A full playlist round-trip, against a throwaway state directory.

        Points playlists.py at a temp dir for the duration, so running the
        tests never touches — or reveals — anyone's real library. Exercises the
        thing the module is FOR: keep, add, reorder, share, and the rule that
        somebody else's key cannot touch your playlist.
        """
        import tempfile
        from pathlib import Path as _P
        pl = _playlists()
        keep = (pl.PL_DIR, pl.SHARE_INDEX)
        with tempfile.TemporaryDirectory() as tmp:
            pl.PL_DIR = _P(tmp) / 'playlists'
            pl.SHARE_INDEX = _P(tmp) / 'shares.json'
            try:
                mine, theirs = pl.new_guest_key(), pl.new_guest_key()
                t = lambda n, src='bandcamp': {'source': src, 'id': str(n),
                                               'name': f'track {n}'}
                doc = pl.create(name='Test set', tracks=[t(1), t(2)], guest=mine)
                pl.add(id=doc['id'], track=t(3), guest=mine)
                dup = pl.add(id=doc['id'], track=t(3), guest=mine)
                if dup['added'] or len(dup['tracks']) != 3:
                    return f'a duplicate was appended: {len(dup["tracks"])} tracks'
                back = pl.reorder(id=doc['id'], keys=['bandcamp:3'], guest=mine)
                if [x['id'] for x in back['tracks']] != ['3', '1', '2']:
                    return f'reorder put them in {[x["id"] for x in back["tracks"]]}'
                sh = pl.share(id=doc['id'], on=True, listed=True, guest=mine)
                seen = pl.open_(share=sh['share_id'])
                if seen['mine'] or seen['owner'] is not None:
                    return 'a shared read leaked the owner'
                copy = pl.copy(share=sh['share_id'], guest=theirs)
                if len(copy['tracks']) != 3 or copy['owner'] == doc['owner']:
                    return 'copy did not become the copier\'s own'
                try:
                    pl.edit(id=doc['id'], name='hijacked', guest=theirs)
                    return 'another key was allowed to edit the playlist'
                except pl.PlaylistError:
                    pass
                if not [x for x in pl.feed()['items'] if x['share_id'] == sh['share_id']]:
                    return 'a listed playlist is missing from the directory'
                pl.share(id=doc['id'], on=False, guest=mine)
                try:
                    pl.open_(share=sh['share_id'])
                    return 'a revoked link still opens'
                except pl.PlaylistError:
                    pass
                pl.delete(id=doc['id'], guest=mine)
                if pl.mine(guest=mine)['count']:
                    return 'delete left the playlist behind'
            except Exception as e:                              # noqa: BLE001
                return f'{type(e).__name__}: {e}'
            finally:
                pl.PL_DIR, pl.SHARE_INDEX = keep
        return 'ok'

    def register(self, app_url=None, api_url=None, owner=None,
                 gateway='https://modc2.com') -> dict:
        app_url = app_url or f'http://localhost:{self.app_port}'
        api_url = api_url or f'http://localhost:{self.port}'
        try:
            ns = m.mod('server.namespace')()
            ns.reg('crates', app_url)
            ns.reg_app('crates', app_url, owner=owner or '',
                       port=self.app_port, api_url=api_url)
            public = f'{gateway.rstrip("/")}/crates'
            print(f'crates registered → {public}  (app: {app_url}, api: {api_url})')
            return {'ok': True, 'gateway': public, 'app': app_url, 'api': api_url}
        except Exception as e:
            print(f'crates: gateway registration failed: {e}')
            return {'ok': False, 'error': str(e), 'app': app_url, 'api': api_url}

    def deregister(self) -> dict:
        try:
            m.mod('server.namespace')().dereg_app('crates')
            return {'ok': True, 'deregistered': 'crates'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}
