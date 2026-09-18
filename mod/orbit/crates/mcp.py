#!/usr/bin/env python3
"""crates mcp — the crate and the playlists, as tools an agent can call.

Two halves, one rule: **digging is open, owning is attributed.** Anyone can
search five platforms, resolve a link and fetch a stream URL. Keeping a
playlist takes a credential — a mod-protocol ``token`` or the ``guest`` key the
console holds — and every tool that touches a playlist derives its owner from
that credential and from nothing else.

The tool an agent will reach for most is ``crates_playlist_add``: it takes a
phrase, not an id. "Put Four Tet's Baby in my Friday set" is one call, because
the tool searches the crate itself, prefers a hit that can actually be decoded
onto a deck over one that is merely metadata, and appends it.

Self-contained JSON-RPC 2.0 on the stdlib, no ``mcp`` package:

    python3 mcp.py                     # stdio — one JSON message per line
    python3 mcp.py --http --port 50790 # Streamable HTTP — POST /mcp

serve.py mounts :func:`handle` at /mcp, so the tools, the REST routes and the
console all run the same code and cannot drift apart.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    # Appended, never prepended: this directory holds a mod.py that would
    # shadow the protocol's own `mod` package for anything importing us.
    sys.path.append(HERE)

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'
VERSION = '1.0.0'

INSTRUCTIONS = (
    'A record crate and a playlist library. crates_search digs Spotify, '
    'Bandcamp, SoundCloud, YouTube and the Internet Archive at once; '
    'crates_resolve turns any link from those into the track, album or '
    'playlist it names; crates_stream says where a track\'s audio actually is '
    '(everything but Spotify hands over decodable audio). The playlist half is '
    "the user's own: crates_playlists lists what they keep, "
    'crates_playlist_create starts one, and crates_playlist_add takes a plain '
    'phrase — it searches the crate itself and appends the best playable hit, '
    'so adding a track is one call and not three. crates_playlist_share mints '
    'a read-only link anyone can open or copy, and crates_playlist_feed is '
    'what has been shared publicly here. Playlist tools need a credential: '
    'pass token (a mod-protocol token) or guest (the key the console keeps) on '
    'each call, or set CRATES_TOKEN / CRATES_GUEST in the server\'s '
    'environment. crates_whoami says which one is in effect.'
)


def _mod():
    """The Mod instance — every tool is one of its methods, so the MCP surface
    and the REST surface cannot answer differently."""
    if '_mod_instance' not in globals():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'crates_anchor', os.path.join(HERE, 'mod.py'))
        anchor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(anchor)
        globals()['_mod_instance'] = anchor.Mod()
    return globals()['_mod_instance']


def _creds(a, token=None):
    """The caller's credential: the tool's own arguments first, then the
    request's Authorization header, then the environment."""
    return {
        'token': a.pop('token', None) or token or os.environ.get('CRATES_TOKEN') or None,
        'guest': a.pop('guest', None) or os.environ.get('CRATES_GUEST') or None,
    }


# ── the crate ──

def _t_search(a, token):
    return _mod().search(q=a.get('q'), source=a.get('source', 'all'),
                         kind=a.get('kind', 'track'), limit=a.get('limit', 20))


def _t_resolve(a, token):
    return _mod().resolve(url=a.get('url'))


def _t_stream(a, token):
    return _mod().stream(source=a.get('source'), id=a.get('id'),
                         track=a.get('track'))


def _t_discover(a, token):
    return _mod().discover(tag=a.get('tag', 'electronic'),
                           slice=a.get('slice', 'top'), size=a.get('size', 24))


def _t_platforms(a, token):
    return _mod().platforms()


# ── the library ──

def _t_whoami(a, token):
    return _mod().whoami(**_creds(a, token))


def _t_guest_key(a, token):
    return _mod().guest_key()


def _t_playlists(a, token):
    return _mod().playlists(**_creds(a, token))


def _t_playlist(a, token):
    return _mod().playlist_open(id=a.get('id'), share=a.get('share'),
                                **_creds(a, token))


def _t_create(a, token):
    return _mod().playlist_new(name=a.get('name'), note=a.get('note', ''),
                               tracks=a.get('tracks'), **_creds(a, token))


def _t_edit(a, token):
    return _mod().playlist_edit(id=a.get('id'), name=a.get('name'),
                                note=a.get('note'), **_creds(a, token))


def _t_delete(a, token):
    return _mod().playlist_delete(id=a.get('id'), **_creds(a, token))


def _t_add(a, token):
    return _mod().playlist_add(id=a.get('id'), q=a.get('q'), url=a.get('url'),
                               source=a.get('source'), track=a.get('track'),
                               tracks=a.get('tracks'), at=a.get('at'),
                               **_creds(a, token))


def _t_remove(a, token):
    return _mod().playlist_remove(id=a.get('id'), key=a.get('key'),
                                  index=a.get('index'), **_creds(a, token))


def _t_move(a, token):
    return _mod().playlist_move(id=a.get('id'), to=a.get('to'),
                                **{'from': a.get('from')}, **_creds(a, token))


def _t_reorder(a, token):
    return _mod().playlist_reorder(id=a.get('id'), keys=a.get('keys'),
                                   **_creds(a, token))


def _t_share(a, token):
    return _mod().playlist_share(id=a.get('id'), on=a.get('on', True),
                                 listed=a.get('listed', False),
                                 **_creds(a, token))


def _t_copy(a, token):
    return _mod().playlist_copy(share=a.get('share'), name=a.get('name'),
                                **_creds(a, token))


def _t_feed(a, token):
    return _mod().playlist_feed(limit=a.get('limit', 30))


def _s(props, required=()):
    return {'type': 'object', 'properties': props, 'required': list(required)}


_STR = {'type': 'string'}
_NUM = {'type': 'integer'}
_CRED = {
    'token': {'type': 'string', 'description':
              'A mod-protocol token. Identifies the owner by signature; the '
              'same playlists then follow that wallet anywhere.'},
    'guest': {'type': 'string', 'description':
              'A guest key from crates_guest_key — the credential for a user '
              'with no wallet. Whoever holds it owns those playlists.'},
}

TOOLS = [
    {
        'name': 'crates_search',
        'description': (
            'Search Spotify, Bandcamp, SoundCloud, YouTube and the Internet '
            'Archive at once, or one of them. Results interleave so the first '
            'screen is a mix rather than one platform\'s page, and each source '
            'reports its own failure — one platform being down never empties '
            'the crate. A pasted link resolves instead of searching.'),
        'inputSchema': _s({
            'q': {**_STR, 'description': 'What to look for, or a link to resolve.'},
            'source': {**_STR, 'description':
                       'all | spotify | bandcamp | soundcloud | youtube | archive'},
            'kind': {**_STR, 'description': 'track | album | artist | playlist'},
            'limit': _NUM,
        }, ['q']),
        'fn': _t_search,
    },
    {
        'name': 'crates_resolve',
        'description': (
            'Turn one Spotify, Bandcamp, SoundCloud, YouTube or archive.org '
            'link into what it names — a track, or an album/playlist with its '
            'tracks listed.'),
        'inputSchema': _s({'url': _STR}, ['url']),
        'fn': _t_resolve,
    },
    {
        'name': 'crates_stream',
        'description': (
            "Where a track's audio actually is. Bandcamp, SoundCloud, YouTube "
            'and the Archive all hand over decodable audio; Spotify is '
            'DRM-protected and answers with an explanation instead of a URL.'),
        'inputSchema': _s({
            'source': {**_STR, 'description': 'bandcamp | soundcloud | youtube | archive'},
            'id': {**_STR, 'description': "The platform's own id, as search returned it."},
            'track': {**_STR, 'description': 'Bandcamp only: a track id within an album page.'},
        }, ['source', 'id']),
        'fn': _t_stream,
    },
    {
        'name': 'crates_discover',
        'description': "Bandcamp's discover feed for one tag — top, new or rec.",
        'inputSchema': _s({'tag': _STR, 'slice': _STR, 'size': _NUM}),
        'fn': _t_discover,
    },
    {
        'name': 'crates_platforms',
        'description': ('What each platform will and will not do from this '
                        'deployment, keys masked. Check here first when a '
                        'source returns nothing.'),
        'inputSchema': _s({}),
        'fn': _t_platforms,
    },
    {
        'name': 'crates_whoami',
        'description': ('Who the credential you are holding makes you, and what '
                        'that lets you do. Call this before the playlist tools '
                        'if you are not sure a token or guest key is in effect.'),
        'inputSchema': _s({**_CRED}),
        'fn': _t_whoami,
    },
    {
        'name': 'crates_guest_key',
        'description': ('Mint a guest key for a user with no wallet. It is a '
                        'password, not a username: it IS the identity, so hand '
                        'it to the user to keep and pass it on later calls.'),
        'inputSchema': _s({}),
        'fn': _t_guest_key,
    },
    {
        'name': 'crates_playlists',
        'description': ("Every playlist this credential owns — name, track "
                        'count, running time, whether it is shared. Start here.'),
        'inputSchema': _s({**_CRED}),
        'fn': _t_playlists,
    },
    {
        'name': 'crates_playlist',
        'description': ('One playlist in full, with its tracks. Pass id for one '
                        'of yours, or share for a link someone sent you — a '
                        'shared read comes back with mine:false.'),
        'inputSchema': _s({'id': _STR, 'share': _STR, **_CRED}),
        'fn': _t_playlist,
    },
    {
        'name': 'crates_playlist_create',
        'description': ('Start a playlist. Tracks are optional here — the usual '
                        'flow is create, then crates_playlist_add by phrase.'),
        'inputSchema': _s({
            'name': _STR,
            'note': {**_STR, 'description': 'A line about what it is for.'},
            'tracks': {'type': 'array', 'items': {'type': 'object'},
                       'description': 'Crate items, as search returned them.'},
            **_CRED,
        }, ['name']),
        'fn': _t_create,
    },
    {
        'name': 'crates_playlist_add',
        'description': (
            'Add a track to a playlist. Pass q to let this search the crate and '
            'append the best hit that can actually be played, url to add exactly '
            'what a link names, or track/tracks to add items you already have. '
            'Duplicates are ignored rather than appended twice.'),
        'inputSchema': _s({
            'id': _STR,
            'q': {**_STR, 'description': 'A phrase — "four tet baby". Searched here.'},
            'url': {**_STR, 'description': 'A platform link to add.'},
            'source': {**_STR, 'description': 'Narrow the search to one platform.'},
            'track': {'type': 'object'},
            'tracks': {'type': 'array', 'items': {'type': 'object'}},
            'at': {**_NUM, 'description': 'Insert at this position instead of the end.'},
            **_CRED,
        }, ['id']),
        'fn': _t_add,
    },
    {
        'name': 'crates_playlist_remove',
        'description': 'Take a track out, by its key or its 0-based position.',
        'inputSchema': _s({'id': _STR, 'key': _STR, 'index': _NUM, **_CRED}, ['id']),
        'fn': _t_remove,
    },
    {
        'name': 'crates_playlist_move',
        'description': ('Reorder a playlist: move the track at "from" to "to", '
                        'both 0-based. Order is the point of a set list.'),
        'inputSchema': _s({'id': _STR, 'from': _NUM, 'to': _NUM, **_CRED},
                          ['id', 'from', 'to']),
        'fn': _t_move,
    },
    {
        'name': 'crates_playlist_reorder',
        'description': (
            'Reorder a whole playlist in one call: pass the track keys in the '
            'order you want them. Keys the playlist does not have are ignored '
            'and tracks you leave out keep their order at the end, so this can '
            'never lose a track — reach for it instead of many _move calls.'),
        'inputSchema': _s({'id': _STR,
                           'keys': {'type': 'array', 'items': {'type': 'string'},
                                    'description': "Track keys, as crates_playlist "
                                                   'returned them, in the new order.'},
                           **_CRED}, ['id', 'keys']),
        'fn': _t_reorder,
    },
    {
        'name': 'crates_playlist_edit',
        'description': 'Rename a playlist, or change the note on it.',
        'inputSchema': _s({'id': _STR, 'name': _STR, 'note': _STR, **_CRED}, ['id']),
        'fn': _t_edit,
    },
    {
        'name': 'crates_playlist_delete',
        'description': 'Delete one of your playlists, share link and all.',
        'inputSchema': _s({'id': _STR, **_CRED}, ['id']),
        'fn': _t_delete,
    },
    {
        'name': 'crates_playlist_share',
        'description': (
            'Mint a read-only share link for a playlist, or revoke it with '
            'on:false. Anyone holding the link can play and copy it; nobody but '
            'the owner can change it. listed:true also puts it in this '
            "deployment's public directory."),
        'inputSchema': _s({'id': _STR, 'on': {'type': 'boolean'},
                           'listed': {'type': 'boolean'}, **_CRED}, ['id']),
        'fn': _t_share,
    },
    {
        'name': 'crates_playlist_copy',
        'description': ("Copy someone's shared playlist into your own library. "
                        'The copy is yours: editing it does not touch theirs.'),
        'inputSchema': _s({'share': _STR, 'name': _STR, **_CRED}, ['share']),
        'fn': _t_copy,
    },
    {
        'name': 'crates_playlist_feed',
        'description': 'Playlists people have shared publicly on this deployment.',
        'inputSchema': _s({'limit': _NUM}),
        'fn': _t_feed,
    },
]

BY_NAME = {t['name']: t for t in TOOLS}


def tool_list():
    """The tools as MCP wants them — without the Python callable."""
    return [{k: v for k, v in t.items() if k != 'fn'} for t in TOOLS]


def call_tool(name, arguments=None, token=None):
    """Run one tool. Raises KeyError for a name that does not exist."""
    tool = BY_NAME.get(name)
    if tool is None:
        raise KeyError(f'no tool {name!r} — call tools/list')
    return tool['fn'](dict(arguments or {}), token)


# ── JSON-RPC ──

def _result(id_, payload):
    return {'jsonrpc': '2.0', 'id': id_, 'result': payload}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def handle(msg, token=None):
    """One JSON-RPC message in, one message out (or None for a notification)."""
    if not isinstance(msg, dict):
        return _error(None, -32600, 'request must be a JSON object')
    method, id_ = msg.get('method'), msg.get('id')
    params = msg.get('params') or {}

    if method == 'initialize':
        want = params.get('protocolVersion')
        version = want if want in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
        return _result(id_, {
            'protocolVersion': version,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'crates', 'version': VERSION},
            'instructions': INSTRUCTIONS,
        })
    if method in ('notifications/initialized', 'initialized'):
        return None
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        name = params.get('name')
        try:
            out = call_tool(name, params.get('arguments') or {}, token)
        except KeyError as e:
            return _error(id_, -32602, str(e))
        except Exception as e:                                  # noqa: BLE001
            # A tool failing is a result the model should see and work around,
            # not a transport error that kills the conversation.
            out = {'error': f'{type(e).__name__}: {e}'}
        text = json.dumps(out, indent=1, default=str, ensure_ascii=False)
        return _result(id_, {'content': [{'type': 'text', 'text': text}],
                             'isError': bool(isinstance(out, dict) and out.get('error'))})
    if method in ('resources/list', 'prompts/list'):
        return _result(id_, {'resources': [], 'prompts': []})
    return _error(id_, -32601, f'unknown method {method!r}')


def _stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            out = _error(None, -32700, f'parse error: {e}')
        else:
            out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out, default=str) + '\n')
            sys.stdout.flush()


def _http(port, host='0.0.0.0'):
    """Streamable HTTP, standalone. serve.py mounts handle() itself; this is
    for running the MCP server on its own port without the console."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(n) if n else b'{}'
            auth = (self.headers.get('Authorization') or '').replace('Bearer ', '').strip()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as e:
                out = _error(None, -32700, f'parse error: {e}')
            else:
                out = handle(msg, auth or None)
            body = b'' if out is None else json.dumps(out, default=str).encode()
            self.send_response(202 if out is None else 200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    ThreadingHTTPServer((host, port), H).serve_forever()


if __name__ == '__main__':
    if '--http' in sys.argv:
        i = sys.argv.index('--port') if '--port' in sys.argv else -1
        _http(int(sys.argv[i + 1]) if i > 0 else 50790)
    else:
        _stdio()
