#!/usr/bin/env python3
"""spotify mcp — the Spotify Web API as MCP tools, hand-rolled on the stdlib.

Every tool is a thin call into `spotify.Spotify`, the same adapter the CLI and
the REST API use, so an agent, a browser and `m spotify/play` can never drift.

    python3 mcp.py                      # stdio — one JSON-RPC message per line
    python3 mcp.py --http --port 50610  # Streamable HTTP — POST /mcp

No `mcp` package, no fastapi, no spotipy: JSON-RPC 2.0 is a hundred lines and
the dependency would outweigh it.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, never prepended: this directory holds a mod.py that would
    # shadow the protocol's own `mod` package for anything importing us.
    sys.path.append(HERE)

from spotify import Spotify, SpotifyError  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    "Control the caller's own Spotify account: playback, queue, search, "
    'library and playlists. Start with spotify_status — it says whether the '
    'module is logged in, which device is active and what is playing; if it '
    'reports logged_in false, tell the user to run `m spotify/login` (the '
    'OAuth consent has to happen in their browser — you cannot do it for '
    'them). Anywhere a tool takes a track/album/playlist, a free-text phrase '
    'works as well as a spotify: URI or an open.spotify.com link: the module '
    'searches and takes the top hit, so prefer "artist - title" phrasing to '
    'land on the right one. Playback control needs Spotify Premium and an '
    'active device — if a call fails with NO_ACTIVE_DEVICE, call '
    'spotify_devices and spotify_transfer rather than retrying. Catalog '
    'search works logged out when the app has a client secret. spotify_raw is '
    "the escape hatch to any Web API endpoint the tools do not cover."
)


def _sp(args):
    """Per-call bearer (`token`) never leaves this process or hits disk."""
    token = args.pop('token', None) if isinstance(args, dict) else None
    return Spotify(token=token)


def _a(args, *names, default=None):
    for n in names:
        v = args.get(n)
        if v not in (None, ''):
            return v
    return default


# ── tools ──

def _t_status(a):
    sp = _sp(a)
    out = sp.status()
    if out.get('logged_in'):
        try:
            out['now_playing'] = sp.now_playing()
            out['devices'] = sp.devices()['devices']
        except SpotifyError as e:
            out['player_error'] = e.dict()
    return out


def _t_search(a):
    return _sp(a).search(_a(a, 'q', 'query'), type=a.get('type') or 'track',
                         limit=a.get('limit') or 10, market=a.get('market'))


def _t_now_playing(a):
    return _sp(a).now_playing()


def _t_play(a):
    return _sp(a).play(query=_a(a, 'query', 'q'), uri=a.get('uri'),
                       device=a.get('device'), position_ms=a.get('position_ms'),
                       shuffle=a.get('shuffle'))


def _t_pause(a):
    return _sp(a).pause(device=a.get('device'))


def _t_skip(a):
    sp, d = _sp(a), a.get('device')
    return sp.previous(device=d) if str(a.get('direction') or 'next') == 'previous' \
        else sp.next(device=d)


def _t_seek(a):
    return _sp(a).seek(_a(a, 'position_ms'), device=a.get('device'))


def _t_volume(a):
    return _sp(a).volume(_a(a, 'percent', 'volume_percent'), device=a.get('device'))


def _t_mode(a):
    sp, out = _sp(a), {}
    if a.get('shuffle') is not None:
        out.update(sp.shuffle(a['shuffle'], device=a.get('device')))
    if a.get('repeat') is not None:
        out.update(sp.repeat(a['repeat'], device=a.get('device')))
    if not out:
        raise SpotifyError('pass shuffle and/or repeat')
    return out


def _t_devices(a):
    return _sp(a).devices()


def _t_transfer(a):
    return _sp(a).transfer(_a(a, 'device'), play=a.get('play', True))


def _t_queue(a):
    return _sp(a).queue(query=_a(a, 'query', 'q'), uri=a.get('uri'),
                        device=a.get('device'))


def _t_up_next(a):
    return _sp(a).up_next(limit=a.get('limit') or 10)


def _t_recent(a):
    return _sp(a).recent(limit=a.get('limit') or 20)


def _t_top(a):
    return _sp(a).top(type=a.get('type') or 'tracks',
                      time_range=a.get('time_range') or 'medium_term',
                      limit=a.get('limit') or 20)


def _t_saved(a):
    sp = _sp(a)
    target = _a(a, 'query', 'uri')
    if target:
        return sp.save(query=target, remove=bool(a.get('remove')))
    return sp.saved(limit=a.get('limit') or 20, offset=a.get('offset') or 0)


def _t_playlists(a):
    return _sp(a).playlists(limit=a.get('limit') or 50, offset=a.get('offset') or 0)


def _t_playlist(a):
    return _sp(a).playlist(_a(a, 'id', 'playlist', 'uri'), limit=a.get('limit') or 100)


def _t_playlist_create(a):
    return _sp(a).playlist_create(_a(a, 'name'), public=bool(a.get('public')),
                                  description=a.get('description'),
                                  uris=a.get('tracks') or a.get('uris'))


def _t_playlist_edit(a):
    sp, pid = _sp(a), _a(a, 'id', 'playlist', 'uri')
    tracks = a.get('tracks') or a.get('uris')
    if a.get('remove'):
        return sp.playlist_remove(pid, tracks)
    return sp.playlist_add(pid, tracks, position=a.get('position'))


def _t_lookup(a):
    return _sp(a).lookup(_a(a, 'uri', 'url', 'id'))


def _t_raw(a):
    return _sp(a).raw(_a(a, 'path'), method=a.get('method') or 'GET',
                      body=a.get('body'), params=a.get('params'))


_DEVICE = {'type': 'string', 'description': 'device name or id (default: the active one)'}
_TARGET = ('a spotify: URI, an open.spotify.com link, a bare id, or free text '
           'to search for (top hit wins)')

TOOLS = {
    'spotify_status': {
        'description': 'Auth state + what is playing + which devices are alive. '
                       'Call this first: it says whether the module is logged '
                       'in and which device playback would land on.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_status,
    },
    'spotify_search': {
        'description': 'Search the Spotify catalog. Works logged out when the '
                       'app has a client secret. Returns flattened items with '
                       'their URIs — feed those to spotify_play or spotify_queue.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': {'type': 'string', 'description': 'search phrase; Spotify field '
                  'filters work too, e.g. "artist:Boards of Canada year:1998"'},
            'type': {'type': 'string', 'description': 'track, artist, album, '
                     'playlist, show, episode — or a comma list (default track)'},
            'limit': {'type': 'integer', 'description': '1–50 (default 10)'},
            'market': {'type': 'string', 'description': 'ISO country, e.g. US'},
        }, 'required': ['q']},
        'handler': _t_search,
    },
    'spotify_now_playing': {
        'description': 'The current track, progress, device, shuffle/repeat state.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_now_playing,
    },
    'spotify_play': {
        'description': 'Start or resume playback. With no argument it resumes; '
                       'with a query/uri it plays that track, album, artist or '
                       'playlist. Needs Premium and an active device.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': _TARGET},
            'uri': {'type': 'string', 'description': 'explicit spotify: URI '
                    '(skips the search)'},
            'device': _DEVICE,
            'position_ms': {'type': 'integer', 'description': 'start offset in ms'},
            'shuffle': {'type': 'boolean', 'description': 'set shuffle before playing'},
        }},
        'handler': _t_play,
    },
    'spotify_pause': {
        'description': 'Pause playback on the active (or named) device.',
        'inputSchema': {'type': 'object', 'properties': {'device': _DEVICE}},
        'handler': _t_pause,
    },
    'spotify_skip': {
        'description': 'Skip to the next or previous track.',
        'inputSchema': {'type': 'object', 'properties': {
            'direction': {'type': 'string', 'enum': ['next', 'previous'],
                          'description': 'default next'},
            'device': _DEVICE,
        }},
        'handler': _t_skip,
    },
    'spotify_seek': {
        'description': 'Jump to a position in the current track.',
        'inputSchema': {'type': 'object', 'properties': {
            'position_ms': {'type': 'integer', 'description': 'milliseconds from start'},
            'device': _DEVICE,
        }, 'required': ['position_ms']},
        'handler': _t_seek,
    },
    'spotify_volume': {
        'description': 'Set playback volume, 0–100.',
        'inputSchema': {'type': 'object', 'properties': {
            'percent': {'type': 'integer', 'description': '0–100'},
            'device': _DEVICE,
        }, 'required': ['percent']},
        'handler': _t_volume,
    },
    'spotify_mode': {
        'description': 'Set shuffle and/or repeat (track | context | off).',
        'inputSchema': {'type': 'object', 'properties': {
            'shuffle': {'type': 'boolean'},
            'repeat': {'type': 'string', 'enum': ['track', 'context', 'off']},
            'device': _DEVICE,
        }},
        'handler': _t_mode,
    },
    'spotify_devices': {
        'description': 'Every device Spotify can currently reach, and which one '
                       'is active. A device only appears once its Spotify app is open.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_devices,
    },
    'spotify_transfer': {
        'description': 'Move playback to another device by name or id. This is '
                       'the fix for a NO_ACTIVE_DEVICE failure.',
        'inputSchema': {'type': 'object', 'properties': {
            'device': {'type': 'string', 'description': 'name or id from spotify_devices'},
            'play': {'type': 'boolean', 'description': 'keep playing (default true)'},
        }, 'required': ['device']},
        'handler': _t_transfer,
    },
    'spotify_queue': {
        'description': 'Add one track to the end of the play queue.',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': _TARGET},
            'uri': {'type': 'string'},
            'device': _DEVICE,
        }},
        'handler': _t_queue,
    },
    'spotify_up_next': {
        'description': 'What is queued after the current item.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': {'type': 'integer', 'description': 'how many to show (default 10)'},
        }},
        'handler': _t_up_next,
    },
    'spotify_recent': {
        'description': 'Recently played tracks, newest first, with timestamps.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': {'type': 'integer', 'description': '1–50 (default 20)'},
        }},
        'handler': _t_recent,
    },
    'spotify_top': {
        'description': "The user's most-played tracks or artists over 4 weeks "
                       '(short_term), ~6 months (medium_term) or years (long_term). '
                       'Use this to ground recommendations in real listening.',
        'inputSchema': {'type': 'object', 'properties': {
            'type': {'type': 'string', 'enum': ['tracks', 'artists']},
            'time_range': {'type': 'string',
                           'enum': ['short_term', 'medium_term', 'long_term']},
            'limit': {'type': 'integer', 'description': '1–50 (default 20)'},
        }},
        'handler': _t_top,
    },
    'spotify_saved': {
        'description': 'Your Library: with no argument it lists saved tracks; '
                       'with a query/uri it saves that track (remove=true unsaves).',
        'inputSchema': {'type': 'object', 'properties': {
            'query': {'type': 'string', 'description': _TARGET + ' — save this one'},
            'uri': {'type': 'string'},
            'remove': {'type': 'boolean', 'description': 'unsave instead of save'},
            'limit': {'type': 'integer'}, 'offset': {'type': 'integer'},
        }},
        'handler': _t_saved,
    },
    'spotify_playlists': {
        'description': "The user's playlists (owned and followed).",
        'inputSchema': {'type': 'object', 'properties': {
            'limit': {'type': 'integer', 'description': '1–50 (default 50)'},
            'offset': {'type': 'integer'},
        }},
        'handler': _t_playlists,
    },
    'spotify_playlist': {
        'description': 'One playlist with its tracks.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': 'playlist id, URI or link'},
            'limit': {'type': 'integer', 'description': 'tracks to return (max 100)'},
        }, 'required': ['id']},
        'handler': _t_playlist,
    },
    'spotify_playlist_create': {
        'description': 'Create a playlist on the account, optionally filled in '
                       'the same call. Private by default.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': {'type': 'string'},
            'description': {'type': 'string'},
            'public': {'type': 'boolean', 'description': 'default false'},
            'tracks': {'type': 'array', 'items': {'type': 'string'},
                       'description': 'URIs or search phrases to add'},
        }, 'required': ['name']},
        'handler': _t_playlist_create,
    },
    'spotify_playlist_edit': {
        'description': 'Add tracks to a playlist (remove=true takes them out). '
                       'Entries may be URIs or search phrases.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': 'playlist id, URI or link'},
            'tracks': {'type': 'array', 'items': {'type': 'string'},
                       'description': 'URIs or search phrases'},
            'remove': {'type': 'boolean'},
            'position': {'type': 'integer', 'description': 'insert index'},
        }, 'required': ['id', 'tracks']},
        'handler': _t_playlist_edit,
    },
    'spotify_lookup': {
        'description': 'Resolve a spotify: URI or open.spotify.com link to the '
                       'full object (track, album, artist, playlist, show, episode).',
        'inputSchema': {'type': 'object', 'properties': {
            'uri': {'type': 'string', 'description': 'URI, link or bare id'},
        }, 'required': ['uri']},
        'handler': _t_lookup,
    },
    'spotify_raw': {
        'description': 'Escape hatch: call any Spotify Web API endpoint with the '
                       "caller's token, e.g. path=/browse/new-releases. Use when "
                       'no tool above covers it.',
        'inputSchema': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'e.g. /me/following?type=artist'},
            'method': {'type': 'string', 'enum': ['GET', 'POST', 'PUT', 'DELETE']},
            'params': {'type': 'object', 'description': 'query parameters'},
            'body': {'type': 'object', 'description': 'JSON body for writes'},
        }, 'required': ['path']},
        'handler': _t_raw,
    },
}


def tool_list():
    return [{'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]


def call_tool(name, arguments=None):
    """In-process tool call — the same path the server takes."""
    tool = TOOLS.get(name)
    if not tool:
        raise SpotifyError(f'unknown tool: {name} — one of {", ".join(TOOLS)}', 404)
    return tool['handler'](dict(arguments or {}))


# ── JSON-RPC 2.0 ──

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def _call(id_, params):
    name = str(params.get('name') or '')
    if name not in TOOLS:
        return _error(id_, -32602, f'unknown tool: {name}')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    try:
        result = call_tool(name, args)
    except SpotifyError as e:
        # Tool failures are *successful* JSON-RPC responses carrying isError,
        # so the model reads the hint (log in / no device / rate limit) and acts.
        return _result(id_, {'content': [{'type': 'text',
                                          'text': json.dumps(e.dict(), indent=2)}],
                             'isError': True, 'structuredContent': e.dict()})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{name} failed: {type(e).__name__}: {e}'}],
                             'isError': True})
    text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _result(id_, out)


def handle(body):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object '
                                   'with a method')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        client = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': client if client in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'spotify', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params)
    return _error(id_, -32601, f'method not found: {method}')


def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


# ── transports ──

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
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()


def serve_http(port, base='/spotify'):
    """Streamable HTTP without SSE: one JSON-RPC message per POST /mcp."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    paths = ('/mcp', base.rstrip('/') + '/mcp')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def _send(self, code, payload, ctype='application/json'):
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header('content-type', ctype)
            self.send_header('content-length', str(len(data)))
            self.send_header('access-control-allow-origin', '*')
            self.send_header('access-control-allow-headers', '*')
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):
            self._send(204, b'', 'text/plain')

        def do_GET(self):
            if self.path.rstrip('/').endswith('/health'):
                return self._send(200, b'ok', 'text/plain')
            self._send(405, b'POST JSON-RPC 2.0 messages to this endpoint', 'text/plain')

        def do_POST(self):
            if self.path.split('?')[0].rstrip('/') not in paths:
                return self._send(404, b'not found', 'text/plain')
            n = int(self.headers.get('content-length') or 0)
            try:
                body = json.loads(self.rfile.read(n) or b'')
            except Exception:
                return self._send(400, _error(None, -32700, 'parse error: body is not JSON'))
            resp = handle(body)
            if resp is None:
                return self._send(202, b'', 'text/plain')
            self._send(200, resp)

        def log_message(self, *a):
            pass

    print(f'spotify mcp on :{port} — POST {paths[1]}, {len(TOOLS)} tools', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        i = argv.index('--port') + 1 if '--port' in argv else -1
        serve_http(int(argv[i] if i > 0 else os.environ.get('MCP_PORT', 50610)),
                   os.environ.get('BASE_PATH', '/spotify'))
    else:
        serve_stdio()
