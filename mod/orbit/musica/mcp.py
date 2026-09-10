#!/usr/bin/env python3
"""musica mcp — the crate, as tools an agent can call.

Sixteen tools over five platforms. The shape of the work is: find something
(``musica_search``, or one of the per-platform browse tools), decide what it is
(``musica_resolve`` on any pasted link), then get at the audio — either a URL
to hand a player (``musica_stream``) or actual bytes on this box
(``musica_fetch``).

The rule that matters is which sources have audio an agent can use. Bandcamp,
SoundCloud, YouTube and the Internet Archive all do. Spotify does not: its
audio is DRM-protected, so a Spotify result is metadata for planning and the
honest move is to search the same track name on one of the other four.

Every tool answers with the module's own function, not a reimplementation —
the console at /musica and this server call the same code, so a tool can never
drift from what the booth actually plays.

Self-contained JSON-RPC 2.0 on the standard library, no ``mcp`` package.

    python3 mcp.py                 # stdio — one JSON message per line
    POST /mcp                      # Streamable HTTP, served by serve.py
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Music, as five catalogues and one signal path. musica_search fans out '
    'over Spotify, Bandcamp, SoundCloud, YouTube and the Internet Archive at '
    'once and interleaves the answers; source= narrows it to one. Four of the '
    'five hand over audio you can actually use — Bandcamp, SoundCloud, '
    'YouTube (via yt-dlp) and archive.org. Spotify is metadata and embeds '
    'only because its audio is DRM-protected; when a Spotify row is the right '
    'track, search its name on the other four to find a copy that plays. '
    'Every row carries `source` and `id`, and that pair is what musica_stream '
    'and musica_fetch take. musica_stream gives a URL: `direct: true` means '
    'the origin allows cross-origin fetches (SoundCloud, archive.org), '
    '`direct: false` means it does not (Bandcamp, YouTube) and `proxy_url` is '
    'the one to use — it always works and honours Range. musica_fetch is the '
    'shortcut when you want the file itself on disk, e.g. to transcribe or '
    'analyse it. Paste any link into musica_resolve rather than parsing it. '
    'The tracks come back with duration, art and, where the platform knows '
    'it, BPM; tempo and key detection itself happens in the browser console '
    'at /musica, which is the other half of this module.'
)

# Downloads land here. Bounded on purpose: an agent asking for a stream URL is
# cheap, an agent asking for bytes is not.
DOWNLOAD_DIR = os.path.join(os.path.expanduser('~'), '.mod', 'musica', 'audio')
MAX_FETCH_MB = 120

_mod = None


def bind(instance):
    """Use an already-constructed ``Mod`` (mod.py calls this)."""
    global _mod
    _mod = instance
    return _mod


def mod():
    """The Mod instance, loaded by path.

    ``mod.py`` is both this module's anchor file and the name of the framework
    package it imports, so our own directory comes off sys.path first —
    otherwise ``import mod`` finds the anchor and imports itself. serve.py
    does the same dance for the same reason.
    """
    global _mod
    if _mod is not None:
        return _mod
    shadow = [p for p in sys.path if p in ('', '.', HERE)]
    for p in shadow:
        sys.path.remove(p)
    try:
        spec = importlib.util.spec_from_file_location(
            'musica_mod', os.path.join(HERE, 'mod.py'))
        anchor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(anchor)
        _mod = anchor.Mod()
    finally:
        sys.path[:0] = shadow
    return _mod


class ToolError(RuntimeError):
    pass


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _num(desc, **extra):
    return {'type': 'number', 'description': desc, **extra}


SOURCE_ARG = _str('spotify, bandcamp, soundcloud, youtube or archive',
                  enum=['spotify', 'bandcamp', 'soundcloud', 'youtube', 'archive'])
ID_ARG = _str('the row\'s `id`: a Spotify id, a Bandcamp page URL, a numeric '
              'SoundCloud id, a YouTube video id, or archive.org '
              '"identifier/filename"')


def _checked(out):
    """Module functions report failure inside their answer; make it an error."""
    if isinstance(out, dict) and out.get('error'):
        raise ToolError(out['error'])
    return out


# ── handlers ─────────────────────────────────────────────────────

def _t_search(a):
    return _checked(mod().search(q=a.get('q'), source=a.get('source') or 'all',
                                 kind=a.get('kind') or 'track',
                                 limit=a.get('limit') or 20))


def _t_resolve(a):
    return _checked(mod().resolve(url=a.get('url')))


def _t_stream(a):
    where = _checked(mod().stream(source=a.get('source'), id=a.get('id'),
                                  track=a.get('track')))
    where['proxy_url'] = _proxy_url(where.get('source'), where.get('id'),
                                    where.get('bc_id'))
    # The same route as a path, for a caller that reached this module through
    # a gateway rather than on this box: join it to whatever host answered.
    where['proxy_path'] = '/musica' + where['proxy_url'].split('/musica', 1)[-1]
    where['note'] = ('fetch `url` directly' if where.get('direct') else
                     'no CORS header on `url` — use `proxy_url` from a browser')
    return where


def _proxy_url(source, ident, track=None) -> str:
    from urllib.parse import urlencode
    base = mod().url().rstrip('/')                     # …:50780/musica
    qs = {'id': ident}
    if track:
        qs['track'] = track
    return f'{base}/api/stream/{source}?{urlencode(qs)}'


def _t_fetch(a):
    """Download one track's audio to this box."""
    import requests
    where = _checked(mod().stream(source=a.get('source'), id=a.get('id'),
                                  track=a.get('track')))
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    name = a.get('name') or f"{where.get('artists') or ''} - {where.get('name') or 'track'}"
    stem = ''.join(c if c.isalnum() or c in ' -_.' else '_' for c in name).strip()[:120]
    ext = _extension(where)
    path = os.path.join(DOWNLOAD_DIR, f'{stem or "track"}.{ext}')
    headers = {'User-Agent': 'Mozilla/5.0'}
    if where.get('referer'):
        headers['Referer'] = where['referer']
    cap = int(float(a.get('max_mb') or MAX_FETCH_MB) * 1024 * 1024)
    got = 0
    try:
        with requests.get(where['url'], headers=headers, stream=True, timeout=60) as up:
            if up.status_code >= 400:
                raise ToolError(f'{where["source"]} answered {up.status_code} for the audio')
            with open(path, 'wb') as fh:
                for chunk in up.iter_content(256 * 1024):
                    got += len(chunk)
                    if got > cap:
                        fh.close()
                        os.remove(path)
                        raise ToolError(f'the file is larger than max_mb '
                                        f'({cap // (1024 * 1024)}MB) — raise it or '
                                        'pick a lossy format')
                    fh.write(chunk)
    except requests.RequestException as e:
        raise ToolError(f'download failed: {e}')
    return {'source': where['source'], 'id': where['id'], 'name': where.get('name'),
            'artists': where.get('artists'), 'duration_ms': where.get('duration_ms'),
            'path': path, 'bytes': got, 'format': where.get('format')}


def _extension(where) -> str:
    """What to call the file. The platform's format label first, then the URL's
    own suffix, then mp3 — a Bandcamp stream URL has no extension at all."""
    fmt = str(where.get('format') or '').lower()
    for name, ext in (('m4a', 'm4a'), ('webm', 'webm'), ('opus', 'opus'),
                      ('mp3', 'mp3'), ('flac', 'flac'), ('ogg', 'ogg'),
                      ('vorbis', 'ogg'), ('wave', 'wav')):
        if name in fmt:
            return ext
    tail = str(where.get('url') or '').split('?')[0].rsplit('/', 1)[-1]
    if '.' in tail and len(tail.rsplit('.', 1)[-1]) <= 4:
        return tail.rsplit('.', 1)[-1]
    return 'mp3'


def _t_platforms(a):
    return mod().platforms()


def _t_console(a):
    m = mod()
    return {'info': m.info(), 'decks': m.decks(), 'kit': m.kit()}


def _t_youtube_playlist(a):
    return _checked(mod().youtube_playlist(id=a.get('id'), limit=a.get('limit') or 100))


def _t_youtube_channel(a):
    return _checked(mod().youtube_channel(id=a.get('id'), limit=a.get('limit') or 30))


def _t_youtube_video(a):
    return _checked(mod().youtube_video(id=a.get('id')))


def _t_bandcamp_page(a):
    return _checked(mod().bandcamp_page(url=a.get('url')))


def _t_bandcamp_discover(a):
    return _checked(mod().discover(tag=a.get('tag') or 'electronic',
                                   slice=a.get('slice') or 'top',
                                   size=a.get('size') or 24))


def _t_soundcloud_playlist(a):
    return _checked(mod().soundcloud_playlist(id=a.get('id'), limit=a.get('limit') or 200))


def _t_soundcloud_user(a):
    return _checked(mod().soundcloud_user(id=a.get('id'), limit=a.get('limit') or 50))


def _t_archive_item(a):
    return _checked(mod().archive_item(id=a.get('id')))


def _t_archive_collection(a):
    return _checked(mod().archive_collection(id=a.get('collection') or 'netlabels',
                                             q=a.get('q') or '',
                                             limit=a.get('limit') or 30))


def _t_spotify(a):
    m, kind = mod(), (a.get('kind') or 'track').lower()
    fn = {'track': m.track, 'album': m.album, 'artist': m.artist,
          'playlist': m.playlist}.get(kind)
    if not fn:
        raise ToolError('kind must be track, album, artist or playlist')
    return _checked(fn(id=a.get('id')))


TOOLS = {
    'musica_search': {
        'description': 'Search every music platform at once and get one '
                       'interleaved list back. source=all (default) fans out '
                       'over Spotify, Bandcamp, SoundCloud, YouTube and the '
                       'Internet Archive in parallel, each reporting its own '
                       'error so one platform being down never empties the '
                       'list; `sources` in the answer says how each did. Rows '
                       'carry source, id, name, artists, duration_ms, art, url '
                       'and streamable — Spotify rows are the ones where '
                       'streamable is false, because that audio is DRM-locked. '
                       'A pasted link here is resolved instead of searched.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': _str('what to look for — words, or a link to any of the five'),
            'source': _str('one platform, or all (default)',
                           enum=['all', 'spotify', 'bandcamp', 'soundcloud',
                                 'youtube', 'archive']),
            'kind': _str('track (default), album, artist or playlist. Bandcamp '
                         'also takes all; archive.org indexes items, so it '
                         'answers with albums whatever you ask for',
                         enum=['track', 'album', 'artist', 'playlist', 'all']),
            'limit': _num('rows per platform (default 20, max 50)')},
            'required': ['q']},
        'handler': _t_search,
    },
    'musica_resolve': {
        'description': 'Turn a pasted link into the thing it names, with its '
                       'tracks. Understands Spotify (URLs and spotify: URIs), '
                       'Bandcamp album and track pages, SoundCloud tracks, sets '
                       'and users, YouTube watch/shorts/youtu.be/playlist/'
                       'channel URLs, and archive.org details or download '
                       'links. Albums, playlists and channels come back with '
                       'their track list, so this is one call rather than a '
                       'search plus a browse.',
        'inputSchema': {'type': 'object', 'properties': {
            'url': _str('the link, exactly as pasted')}, 'required': ['url']},
        'handler': _t_resolve,
    },
    'musica_stream': {
        'description': 'Where one track\'s audio actually is. Returns a URL '
                       'plus the flag that matters: `direct: true` (SoundCloud, '
                       'archive.org) means the origin sends CORS headers and a '
                       'browser can fetch it, `direct: false` (Bandcamp, '
                       'YouTube) means it does not and `proxy_url` — this '
                       'module, Range passthrough — is the URL to use. YouTube '
                       'audio is the best audio-only format, m4a first because '
                       'every browser decodes AAC. Signed URLs expire in hours; '
                       'call again rather than storing one. Spotify returns an '
                       'error saying why, which is DRM.',
        'inputSchema': {'type': 'object', 'properties': {
            'source': SOURCE_ARG, 'id': ID_ARG,
            'track': _str('Bandcamp only: which track on an album page')},
            'required': ['source', 'id']},
        'handler': _t_stream,
    },
    'musica_fetch': {
        'description': 'Download a track\'s audio to this box and return the '
                       'path — the tool to use when you want the bytes rather '
                       'than a URL, e.g. to transcribe, fingerprint or analyse '
                       'the music. Works for Bandcamp, SoundCloud, YouTube and '
                       'archive.org; files land in ~/.mod/musica/audio and are '
                       'capped at 120MB unless max_mb says otherwise.',
        'inputSchema': {'type': 'object', 'properties': {
            'source': SOURCE_ARG, 'id': ID_ARG,
            'track': _str('Bandcamp only: which track on an album page'),
            'name': _str('what to call the file (default "artist - title")'),
            'max_mb': _num('refuse anything larger (default 120)')},
            'required': ['source', 'id']},
        'handler': _t_fetch,
    },
    'musica_youtube_video': {
        'description': 'One YouTube video\'s metadata — title, channel, '
                       'duration, view count and which audio codecs it has. '
                       'Takes a video id or any watch/shorts/youtu.be URL. Use '
                       'musica_stream for the audio itself.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('video id or URL')}, 'required': ['id']},
        'handler': _t_youtube_video,
    },
    'musica_youtube_playlist': {
        'description': 'A YouTube playlist, album or mix with its videos '
                       'listed, each ready for musica_stream. Takes a list= id '
                       'or a playlist URL. This is how you turn "that DJ set '
                       'playlist" into a track list.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('playlist id (PL…, OLAK5uy_…) or URL'),
            'limit': _num('videos to list (default 100, max 200)')},
            'required': ['id']},
        'handler': _t_youtube_playlist,
    },
    'musica_youtube_channel': {
        'description': 'A YouTube channel\'s uploads, newest first — an '
                       '@handle, a UC… id or a channel URL. Good for label and '
                       'artist channels, which is where a lot of music that is '
                       'nowhere else lives.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('@handle, UC… id, or channel URL'),
            'limit': _num('videos to list (default 30, max 100)')},
            'required': ['id']},
        'handler': _t_youtube_channel,
    },
    'musica_bandcamp_page': {
        'description': 'One Bandcamp album or track page: every track, with '
                       'whether it streams. Bandcamp only puts some tracks of '
                       'an album online, and `streamable` per track is how you '
                       'find out which. Note this module has to clear a '
                       'JavaScript challenge from datacenter IPs — '
                       'musica_platforms says whether that worked.',
        'inputSchema': {'type': 'object', 'properties': {
            'url': _str('an album or track page URL')}, 'required': ['url']},
        'handler': _t_bandcamp_page,
    },
    'musica_bandcamp_discover': {
        'description': 'Bandcamp\'s own discover feed for one tag — the best '
                       'way to find new music nobody has indexed yet. slice is '
                       'top (bestselling), new, or rec (recommended).',
        'inputSchema': {'type': 'object', 'properties': {
            'tag': _str('a genre tag, e.g. "ambient", "footwork", "jazz"'),
            'slice': _str('top, new or rec', enum=['top', 'new', 'rec']),
            'size': _num('rows (default 24, max 60)')}},
        'handler': _t_bandcamp_discover,
    },
    'musica_soundcloud_playlist': {
        'description': 'A SoundCloud set or album with its tracks hydrated — '
                       'the API hands back bare ids past the first few and this '
                       'fills them in. Takes the numeric playlist id; a set URL '
                       'goes to musica_resolve.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _num('numeric playlist id'),
            'limit': _num('tracks (default 200)')}, 'required': ['id']},
        'handler': _t_soundcloud_playlist,
    },
    'musica_soundcloud_user': {
        'description': 'A SoundCloud user\'s own uploads by numeric user id — '
                       'DJ mixes, edits and unreleased tracks, the material '
                       'that never reaches a store.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _num('numeric user id'),
            'limit': _num('tracks (default 50, max 200)')}, 'required': ['id']},
        'handler': _t_soundcloud_user,
    },
    'musica_archive_item': {
        'description': 'One archive.org item — an album, a concert, a radio '
                       'show — with its tracks. Each track id is '
                       '"identifier/filename", which musica_stream and '
                       'musica_fetch take directly. Where an item holds the '
                       'same music in several formats, the friendliest one per '
                       'track is what comes back (MP3 over FLAC over WAV). This '
                       'is the freest audio here: no key, no challenge, CORS '
                       'open, and much of it public domain or CC.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('archive.org identifier, e.g. gd1977-05-08.sbd…')},
            'required': ['id']},
        'handler': _t_archive_item,
    },
    'musica_archive_collection': {
        'description': 'Browse one collection of the Internet Archive: etree '
                       '(the Live Music Archive — tens of thousands of '
                       'band-sanctioned concert recordings), netlabels (free '
                       'net-label releases), 78rpm (digitised shellac), '
                       'audio_music generally. Optional q narrows within it.',
        'inputSchema': {'type': 'object', 'properties': {
            'collection': _str('collection identifier (default netlabels)'),
            'q': _str('words to narrow it to'),
            'limit': _num('rows (default 30, max 100)')}},
        'handler': _t_archive_collection,
    },
    'musica_spotify': {
        'description': 'One Spotify object by id: a track, an album with its '
                       'tracks, an artist\'s top tracks, or a playlist. '
                       'Metadata only — Spotify audio is DRM-protected and '
                       'cannot be decoded, so use this to decide what you want '
                       'and then find it on the platforms that play. Needs '
                       'Spotify app keys; musica_platforms says whether this '
                       'deployment has any.',
        'inputSchema': {'type': 'object', 'properties': {
            'kind': _str('track, album, artist or playlist',
                         enum=['track', 'album', 'artist', 'playlist']),
            'id': _str('the Spotify id')}, 'required': ['kind', 'id']},
        'handler': _t_spotify,
    },
    'musica_platforms': {
        'description': 'What each of the five platforms will and will not do '
                       'from this deployment, keys masked: whether Spotify has '
                       'credentials, whether Bandcamp\'s JavaScript challenge '
                       'has been cleared here, which SoundCloud client_id is in '
                       'use, whether yt-dlp is installed and what it last '
                       'failed with. Read this first when a source returns '
                       'nothing — it usually explains why.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_platforms,
    },
    'musica_console': {
        'description': 'The other half of this module: the browser DJ booth at '
                       '/musica. Returns its URL and what it is — two decks '
                       'with beatmatch, EQ, filters, loops and key matching, a '
                       'step sequencer and piano roll, and the mixer\'s full '
                       'signal chain and synth kit. Tempo and key detection '
                       'happens there, in the tab, not here.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_console,
    },
}


# ── JSON-RPC ─────────────────────────────────────────────────────

def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def tool_list():
    return [{'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]


def call_tool(name, args):
    """Run one tool by name. Shared with the CLI (``m musica/mcp``), so a
    tool and the function behind it cannot drift apart."""
    tool = TOOLS.get(name)
    if not tool:
        raise ToolError(f'no tool named {name!r} — {", ".join(TOOLS)}')
    args = {k: v for k, v in (args or {}).items() if v is not None}
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise ToolError(f'{name} needs {required}')
    return tool['handler'](args)


def _call(id_, params):
    name = (params or {}).get('name')
    args = (params or {}).get('arguments') or {}
    try:
        out = call_tool(name, args)
        return _result(id_, {
            'content': [{'type': 'text',
                         'text': json.dumps(out, default=str, indent=2)}],
            'structuredContent': out if isinstance(out, dict) else None,
            'isError': False})
    except ToolError as e:
        return _result(id_, {'content': [{'type': 'text', 'text': str(e)}],
                             'isError': True})
    except TypeError as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'bad arguments for {name}: {e}'}],
                             'isError': True})
    except Exception as e:                                      # noqa: BLE001
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{type(e).__name__}: {e}'}],
                             'isError': True})


def handle(body):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        v = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'musica', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params)
    return _error(id_, -32601, f'method not found: {method}')


def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    if '--tools' in sys.argv:                    # what an agent would see
        print(json.dumps(tool_list(), indent=2))
    else:
        serve_stdio()
