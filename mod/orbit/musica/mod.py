"""
musica — a DJ booth and a pattern studio in one browser tab.

Two decks with waveforms, beatmatch, three-band EQ, a bipolar filter, beat
loops and hot cues; a crossfader into a limited master you can record; and
underneath it a step sequencer and piano roll in the FL Studio idiom, running
off the same clock as the decks.

All the audio work happens client side. Files are decoded in the browser and
mixed through Web Audio, and tempo and key are detected locally — nothing you
drop on a deck is uploaded anywhere. This module's own jobs are to serve the
console and to answer the Spotify half from *your* API keys, kept in
``~/.mod/musica/keys.json`` rather than in the repo.

The crate reaches three platforms. Spotify is metadata and embeds only: its
streamed audio is DRM-protected and cannot be routed through Web Audio, so a
Spotify find is for planning a set. Bandcamp and SoundCloud both stream plain
MP3 to their own web players, and those streams CAN be decoded — so a Bandcamp
or SoundCloud track loads straight onto a deck, gets analysed for tempo and
key, and mixes like a file. ``platforms.py`` owns the two keyless adapters.

This is the anchor file: the orbit loader imports it by path and instantiates
``Mod``. Everything the module exposes to the CLI, the gateway and other
modules is a public method on that class.

CLI:
    m musica                   # null call → info()
    m musica/play              # serve the console and open a browser
    m musica/serve             # run it under pm2, then register the route
    m musica/url               # where it is
    m musica/decks             # the signal chain, deck by deck
    m musica/kit               # the sequencer's voices
    m musica/set_key client_id=… client_secret=…      # Spotify app keys
    m musica/search q="four tet"                          # every platform at once
    m musica/search q="four tet" source=bandcamp kind=album
    m musica/resolve url=https://fourtet.bandcamp.com/album/three
    m musica/stream source=soundcloud id=2176707750      # where the MP3 is
    m musica/platforms         # what each platform will and won't do here
    m musica/test              # files + JS syntax + engine smoke test
    m musica/kill              # stop it
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
            'musica_platforms', str(MODULE_DIR / 'platforms.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        globals()['_platforms_mod'] = module
    return globals()['_platforms_mod']


def _spotify():
    """Load ``spotify.py`` by path.

    Importing it by name would go through sys.path, where this module's own
    directory sits next to a framework package also called ``mod`` — loading by
    path keeps that collision out of the picture.
    """
    if '_spotify_mod' not in globals():
        spec = importlib.util.spec_from_file_location(
            'musica_spotify', str(MODULE_DIR / 'spotify.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        globals()['_spotify_mod'] = module
    return globals()['_spotify_mod']


SOURCES = ('spotify', 'bandcamp', 'soundcloud')


class Mod:
    description = ('A DJ booth and an FL-style pattern studio in one browser '
                   'tab — two decks, a mixer, a sequencer, and a crate that '
                   'searches Spotify, Bandcamp and SoundCloud at once. Bandcamp '
                   'and SoundCloud tracks load straight onto a deck.')

    # What the HTTP API exposes. Every public method is a function of this
    # module, but the API answers from the public gateway, so it serves only the
    # ones that read: serve/kill/set_key and friends stay on the CLI.
    API_FNS = ('info', 'health', 'readme', 'url', 'path', 'files', 'decks',
               'kit', 'keys', 'search', 'track', 'playlist', 'album', 'artist',
               'my_playlists', 'spotify_status', 'platforms', 'resolve',
               'stream', 'discover', 'bandcamp_page', 'soundcloud_playlist',
               'soundcloud_user')

    # The mixer's signal chain, in order. The console builds exactly this graph
    # per deck; keeping the description here means `m musica/decks` and the code
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

    def __init__(self, key='musica', network='testnet'):
        self.key = m.key(key)
        self.network = network
        self.module_dir = MODULE_DIR
        cfg = self._config()
        self.port = int(cfg.get('port', 50780))
        self.app_port = int(cfg.get('app_port', 50780))

    def _config(self) -> dict:
        try:
            with (MODULE_DIR / 'config.json').open() as fh:
                return json.load(fh)
        except Exception:
            return {}

    # ── module surface ───────────────────────────────────────────────────

    def forward(self, action=None, **kwargs):
        """CLI entry: ``m musica <action> [args]``."""
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
            'name': 'musica',
            'title': 'MUSICA — decks, mixer, sequencer',
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
        return {'ok': True, 'module': 'musica'}

    def readme(self) -> str:
        path = self.module_dir / 'README.md'
        return path.read_text() if path.exists() else ''

    def url(self, gateway=None) -> str:
        """Where the console lives."""
        base = self._config().get('base_path', '/musica')
        if gateway:
            return f'{str(gateway).rstrip("/")}{base}'
        return f'http://localhost:{self.app_port}{base}'

    def api_url(self, gateway=None) -> str:
        """Where the module's functions answer: ``/api/musica``."""
        if gateway:
            return f'{str(gateway).rstrip("/")}/api/musica'
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
            'streams': 'Bandcamp and SoundCloud tracks decode into a deck; '
                       'Spotify is DRM-protected and stays metadata + embeds',
        }

    def set_key(self, client_id=None, client_secret=None, soundcloud_client_id=None, **_) -> dict:
        """Store platform credentials in ``~/.mod/musica/keys.json``.

        Spotify: register an app at https://developer.spotify.com/dashboard —
        the client-credentials grant used here needs no redirect URI. If
        orbit/spotify already holds keys, musica reads those and none are
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
                    'usage': 'm musica/set_key client_id=… client_secret=…'}
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
            return {'error': 'q is required', 'usage': 'm musica/search q="four tet"'}
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
        with ThreadPoolExecutor(max_workers=3) as ex:
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
            return {'error': 'not a Spotify, Bandcamp or SoundCloud link', 'url': url}
        try:
            src, kind, ident = link['source'], link['kind'], link['id']
            if src == 'bandcamp':
                if kind == 'artist':
                    return {**link, 'error': 'a Bandcamp artist page — search their name instead'}
                return pf.bc_page(ident)
            if src == 'soundcloud':
                return pf.sc_resolve(ident)
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

        SoundCloud answers with a signed CDN URL the browser fetches itself
        (``direct: true``). Bandcamp's has no CORS header, so the console
        pulls it through this module's ``/stream/bandcamp?…`` proxy instead
        (``direct: false``). Spotify has no stream to give.
        """
        pf = _platforms()
        try:
            if source == 'soundcloud':
                return pf.sc_stream(id)
            if source == 'bandcamp':
                return pf.bc_stream(id, track_id=track)
        except pf.PlatformError as e:
            return {'error': str(e), 'source': source, 'id': id}
        if source == 'spotify':
            return {'error': 'Spotify audio is DRM-protected and cannot be decoded — '
                             'preview it in the embed, then load the file or find it '
                             'on Bandcamp or SoundCloud', 'source': source, 'id': id}
        return {'error': 'source must be bandcamp or soundcloud'}

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
        ok = self._pm2_start('musica.app', cmd, cwd=str(self.module_dir), env=env)
        return {'app': f'http://localhost:{app_port}', 'pm2': 'musica.app', 'ok': ok}

    # The mixing is all client side and the Spotify calls are stateless, so one
    # process answers both halves of the route from the same port.
    def serve_api(self, **_) -> dict:
        return {'ok': True, 'api': self.api_url(), 'pm2': 'musica.app',
                'fns': list(self.API_FNS),
                'note': 'served by musica.app — the console needs no second process'}

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
            'musica_serve', str(self.module_dir / 'serve.py'))
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

        url = f'http://localhost:{port}/musica/'
        print(f'musica serving at {url}')
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
        killed = [n for n in ('musica.api', 'musica.app') if self._pm2_kill(n)]
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
                  'js/app.js']
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
              and not isinstance(checks.get('engine'), str))
        return {'ok': ok, 'checks': checks}

    def register(self, app_url=None, api_url=None, owner=None,
                 gateway='https://modc2.com') -> dict:
        app_url = app_url or f'http://localhost:{self.app_port}'
        api_url = api_url or f'http://localhost:{self.port}'
        try:
            ns = m.mod('server.namespace')()
            ns.reg('musica', app_url)
            ns.reg_app('musica', app_url, owner=owner or '',
                       port=self.app_port, api_url=api_url)
            public = f'{gateway.rstrip("/")}/musica'
            print(f'musica registered → {public}  (app: {app_url}, api: {api_url})')
            return {'ok': True, 'gateway': public, 'app': app_url, 'api': api_url}
        except Exception as e:
            print(f'musica: gateway registration failed: {e}')
            return {'ok': False, 'error': str(e), 'app': app_url, 'api': api_url}

    def deregister(self) -> dict:
        try:
            m.mod('server.namespace')().dereg_app('musica')
            return {'ok': True, 'deregistered': 'musica'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}
