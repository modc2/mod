#!/usr/bin/env python3
"""
store mcp — the same store, for an agent instead of a person.

Every tool here is the same call the console makes and the same call
`python3 mod.py` makes: they all go through `src/`, so there is one
implementation of every rule and three front doors onto it.

    python3 src/mcp.py                 # stdio — one JSON-RPC message per line
    POST http://127.0.0.1:50670/mcp    # the API serves the same handler
    GET  http://127.0.0.1:50670/mcp/schema   # the tool list, as plain JSON

There is no separate MCP port. The API already listens, already knows who is
calling, and already enforces ownership; a second process would only be a
second copy of that.

WHO THE AGENT IS
    Over stdio there is no HTTP request to authenticate, so tools run as the
    box's local owner — the same identity `python3 mod.py` uses. Over HTTP the
    API passes in the address it recovered from the caller's token, and a
    remote caller with no token never reaches this file at all. Ownership is
    not re-implemented here: every handler takes the owner it was given and
    hands it to the library, which is what refuses to show you somebody else's
    pictures.

WHAT AN AGENT GETS BESIDES TOOLS
    Resources are the nouns — the manual at store://docs and each picture the
    caller owns at store://image/<id> — for a client that lets a person attach
    context by hand, and so a model can look at an image without spending a
    tool call on it. Prompts are two, because there are two ways to share and
    picking the wrong one is the mistake this module exists to prevent.

    Tools that are about a picture return the picture: `image` content blocks,
    not a paragraph describing one. `store_claim` most of all — by the time it
    answers, the code is spent, and that response is the only copy anyone is
    going to get.

THE ONE TOOL THAT DESTROYS SOMETHING
    `store_claim` redeems a code, and redeeming is the thing that spends it. An
    agent that calls it to "check whether the link works" has just used the
    link up. `store_peek` is the one that asks without spending, and its
    description says so — the two are deliberately separate tools rather than
    one tool with a flag, because a flag defaults and a wrong default here
    cannot be undone.
"""
import base64
import json
import os
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent.parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from src import grants, identity, library, links, qr, resolve  # noqa: E402
from src.library import StoreError  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

# Base64 inflates by a third and a tool result travels through the model's
# context, so a picture is inlined only if it is small enough to be worth a
# few thousand tokens. Past this the answer is a URL and a refusal that says
# which URL, rather than a context window spent on one photograph.
MAX_INLINE_BYTES = int(os.environ.get('STORE_SHARE_MAX_INLINE', 1_500_000))

INSTRUCTIONS = (
    'store shares pictures two ways, and they are not interchangeable. '
    'PUBLISH (store_publish) gives an image a permanent public URL with no '
    'credential, no expiry and no audience — anyone who ever sees the link '
    'sees the picture, and unpublishing does not un-share the copies already '
    'made. GRANT (store_grant) mints a code good for exactly one fetch and '
    'exactly N seconds, meant to be shown as a QR code to a person who is '
    'in the room; the first fetch burns it and the second gets 410. Prefer a '
    'grant for anything handed to one person, and publish only when a '
    'permanent open link is what was actually asked for. '
    'The url a grant returns is a PAGE — it explains the code, counts the '
    'timer down and has the button that spends it — so it is safe to paste '
    'into a chat, unlike the raw bytes_url which is spent by whatever fetches '
    'it first. store_peek asks whether a code is still good WITHOUT spending '
    'it; store_claim spends it. Uploads must be real png/jpeg/gif/webp/bmp '
    'bytes — the format is sniffed, filenames are not believed, and SVG is '
    'refused outright.'
)


# ── helpers ──────────────────────────────────────────────────────────

def _owner(explicit=None) -> str:
    return explicit or identity.local_owner()


def _int(args, name, default):
    try:
        return int(args.get(name, default))
    except (TypeError, ValueError):
        return default


def _need(args, name):
    value = args.get(name)
    if value in (None, ''):
        raise StoreError(f'{name} is required', 400)
    return value


def _first(args, *names):
    """The first of several spellings a caller might have reached for.

    Models guess argument names from the sentence they are answering, so
    `picture`, `image` and `id` all arrive in practice for the same slot.
    Accepting the synonyms costs a tuple and saves a retry that would
    otherwise burn a turn re-reading the schema.
    """
    for name in names:
        value = args.get(name)
        if value not in (None, ''):
            return value
    return None


def _image_ref(args, owner, public_too=False):
    """The image id an agent meant — by id, prefix, name or `latest`."""
    return resolve.image(_first(args, 'id', 'image', 'picture', 'name'),
                         owner, public_too=public_too)


def _code_ref(args, owner):
    """A whole code as handed over, or a prefix of one this owner minted."""
    reference = str(_first(args, 'code', 'grant', 'link') or '').strip()
    if reference and grants.peek(reference):
        return reference
    return resolve.code(reference, owner)


def _ttl(args):
    return resolve.ttl(_first(args, 'ttl_seconds', 'for', 'ttl', 'duration',
                              'seconds'))


def _image_content(data: bytes, mime: str):
    """
    An MCP image content block — the picture itself, not a sentence about it.

    This module's entire subject is pictures, so a tool result that describes
    one and cannot show it is the wrong shape. `image` blocks are in the spec
    for exactly this and every client that renders anything renders them.
    """
    return {'type': 'image',
            'data': base64.b64encode(data).decode('ascii'),
            'mimeType': mime or 'application/octet-stream'}


# ── tools ────────────────────────────────────────────────────────────

def _t_info(args, owner):
    return {
        'what': 'image sharing — permanent public links, and one-time codes '
                'that expire after N seconds',
        'you_are': owner,
        'state': str(library.HOME),
        'share_base': links.BASE,
        'stats': library.stats(),
        'live_grants': len(grants.listing(owner)),
        'formats': ['image/png', 'image/jpeg', 'image/gif', 'image/webp',
                    'image/bmp'],
        'max_bytes': library.MAX_BYTES,
        'ttl_seconds': {'min': grants.MIN_TTL, 'max': grants.MAX_TTL,
                        'default': grants.DEFAULT_TTL},
        'qr_encoder': qr.available(),
    }


def _t_images(args, owner):
    return {'owner': owner,
            'images': [links.decorate_image(r) for r in library.listing(
                owner, _int(args, 'limit', 50), _int(args, 'offset', 0),
                bool(args.get('public_only')))]}


def _t_public(args, owner):
    return {'images': [links.decorate_image(r) for r in library.public_listing(
        _int(args, 'limit', 50), _int(args, 'offset', 0))]}


def _t_image(args, owner):
    image_id = _image_ref(args, owner, public_too=True)
    record = library.record(image_id, owner) or library.public_record(image_id)
    if record is None:
        raise StoreError('no such image', 404)
    return links.decorate_image(record)


def _t_view(args, owner):
    """The picture itself, so the model can look at what it is about to share."""
    image_id = _image_ref(args, owner, public_too=True)
    record = library.record(image_id, owner) or library.public_record(image_id)
    if record is None:
        raise StoreError('no such image', 404)
    data = library.read(image_id)
    if len(data) > MAX_INLINE_BYTES:
        raise StoreError(
            f'{record["name"]} is {len(data)} bytes, past the '
            f'{MAX_INLINE_BYTES} inline ceiling — read it from '
            f'{links.image_bytes(image_id) if record["public"] else "its file"} '
            f'instead', 413)
    return {'content': [_image_content(data, record['mime']),
                        {'type': 'text',
                         'text': json.dumps(links.decorate_image(record),
                                            indent=2, default=str)}],
            'structuredContent': links.decorate_image(record)}


def _t_health(args, owner):
    """Whether the index still agrees with the disk."""
    missing = [row['id'] for row in library.public_listing(limit=500)
               if not library.blob_path(row['id']).exists()]
    return {'ok': not missing, 'missing_blobs': missing,
            'state': str(library.HOME), 'qr_encoder': qr.available(),
            'live_grants': len(grants.listing(owner)), **library.stats()}


def _t_add(args, owner):
    name = args.get('name') or ''
    if args.get('path'):
        source = Path(str(args['path'])).expanduser()
        if not source.is_file():
            raise StoreError(f'no such file: {source}', 404)
        data = source.read_bytes()
        name = name or source.name
    elif args.get('base64'):
        try:
            data = base64.b64decode(str(args['base64']), validate=True)
        except Exception:
            raise StoreError('base64 did not decode', 400)
    else:
        raise StoreError('pass a path on this box, or base64 image bytes', 400)
    return links.decorate_image(library.put(
        data, name=name, owner=owner, public=bool(args.get('public'))))


def _t_publish(args, owner):
    return links.decorate_image(
        library.publish(_image_ref(args, owner), owner, True))


def _t_unpublish(args, owner):
    return links.decorate_image(
        library.publish(_image_ref(args, owner), owner, False))


def _t_delete(args, owner):
    return library.remove(_image_ref(args, owner), owner)


def _t_grant(args, owner):
    seconds = _ttl(args)
    grant = grants.create(_image_ref(args, owner), owner, seconds)
    out = links.decorate_grant(grant)
    out['show_the_person'] = out['page_url']
    out['good_for'] = f'one fetch, {resolve.human_duration(seconds)}'
    if args.get('qr_ascii') and qr.available():
        out['qr_ascii'] = qr.ascii_art(out['page_url'])
    return out


def _t_qr(args, owner):
    """A picture of a code's page link. Rendering it spends nothing."""
    if not qr.available():
        raise StoreError(
            'no QR encoder on this box (pip install segno) — the page_url on '
            'the grant still works and is the thing that matters', 501)
    code = _code_ref(args, owner)
    if not grants.peek(code):
        raise StoreError('no such grant', 404)
    page = links.grant_page(code)
    svg = qr.svg(page, scale=_int(args, 'scale', 6))
    return {'content': [_image_content(svg.encode('utf-8'), 'image/svg+xml'),
                        {'type': 'text',
                         'text': f'QR for {page} — showing it spends nothing; '
                                 f'the button on the page it opens does'}],
            'structuredContent': {'code': code, 'page_url': page,
                                  'bytes': len(svg),
                                  'spent_by_rendering': False}}


def _t_share(args, owner):
    """Store-if-needed and mint, because handing someone a picture is one act."""
    name = args.get('name') or ''
    source = args.get('path')
    if source:
        path = Path(str(source)).expanduser()
        if not path.is_file():
            raise StoreError(f'no such file: {path}', 404)
        record = library.put(path.read_bytes(), name=name or path.name,
                             owner=owner)
    elif args.get('base64'):
        try:
            data = base64.b64decode(str(args['base64']), validate=True)
        except Exception:
            raise StoreError('base64 did not decode', 400)
        record = library.put(data, name=name, owner=owner)
    else:
        record = library.record(_image_ref(args, owner), owner)
        if record is None:
            raise StoreError('no such image of yours', 404)

    seconds = _ttl(args)
    out = links.decorate_grant(grants.create(record['id'], owner, seconds))
    out['picture'] = record['name']
    out['show_the_person'] = out['page_url']
    out['good_for'] = f'one fetch, {resolve.human_duration(seconds)}'
    out['not_published'] = ('this picture is still private — the code is the '
                            'only way in, and it dies on first use or on the '
                            'clock')
    if args.get('qr_ascii') and qr.available():
        out['qr_ascii'] = qr.ascii_art(out['page_url'])
    return out


def _t_grants(args, owner):
    return {'owner': owner,
            'grants': [links.decorate_grant(g) for g in grants.listing(
                owner, bool(args.get('all')), _int(args, 'limit', 50))]}


def _t_peek(args, owner):
    grant = grants.peek(_code_ref(args, owner))
    if not grant:
        raise StoreError('no such grant', 404)
    grant.pop('image', None)
    grant.pop('owner', None)
    verdict = ('still good' if grant['live'] else
               'already used' if grant['claimed'] else 'expired')
    return {**grant, 'verdict': verdict, 'spent_by_asking': False,
            'time_left': resolve.human_duration(grant['seconds_left'])}


def _t_claim(args, owner):
    """This is the destructive one. It is a separate tool for that reason."""
    grant = grants.claim(_code_ref(args, owner), claimed_by='mcp')
    record = library.record(grant['image'], grant['owner']) or {}
    data = library.read(grant['image'])
    out = {**grant, 'bytes': len(data), 'mime': record.get('mime'),
           'name': record.get('name'), 'burned': True}
    if args.get('out'):
        target = Path(str(args['out'])).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        out['written'] = str(target)

    # The code is spent, so this response is the only copy anyone gets. Hand
    # back the picture itself rather than a paragraph about it — a claim that
    # returns only metadata has destroyed the thing it was asked to fetch.
    blocks = []
    if len(data) <= MAX_INLINE_BYTES:
        blocks.append(_image_content(data, record.get('mime')))
    else:
        out['note'] = (f'{len(data)} bytes is past the {MAX_INLINE_BYTES} '
                       f'inline ceiling, so the picture is not in this '
                       f'response')
    if not args.get('out') and len(data) > MAX_INLINE_BYTES:
        out['warning'] = ('the code is spent and the bytes were not saved — '
                          'pass out=<path> next time to keep them')
    blocks.append({'type': 'text',
                   'text': json.dumps(out, indent=2, default=str)})
    return {'content': blocks, 'structuredContent': out}


def _t_revoke(args, owner):
    return grants.revoke(_code_ref(args, owner), owner)


def _t_docs(args, owner):
    from src import docs
    return docs.document(section=args.get('section') or '')


ID_DESC = ('which picture: its id, any unique prefix of that id (four '
           'characters is usually enough), the name it was stored under, or '
           '"latest" for the most recent one. Two matches is an error naming '
           'both, never a guess.')
TTL_DESC = ('how long the code stays good: a number of seconds, or a duration '
            'like "30s", "5m", "2h", "1d". 1 second to 1 day, 60s by default. '
            'Mint it when the person is in front of you rather than minting a '
            'long one early.')
CODE_DESC = ('the grant code — the whole thing as it was handed to you, or a '
             'unique prefix of one you minted yourself')

TOOLS = {
    'store_info': {
        'description': 'What this store holds and how to reach it: your '
                       'address, how many pictures and live codes there are, '
                       'the accepted formats, the size ceiling and the TTL '
                       'bounds. Start here.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_info,
    },
    'store_docs': {
        'description': 'The module\'s own documentation as structured data — '
                       'the two ways of sharing, every HTTP endpoint, the CLI, '
                       'the environment variables and what it refuses to '
                       'store. Read this before guessing at a URL shape.',
        'inputSchema': {'type': 'object', 'properties': {
            'section': {'type': 'string', 'description':
                        'one of: sharing, endpoints, cli, env, safety, mcp '
                        '(default: all of them)'}}},
        'handler': _t_docs,
    },
    'store_images': {
        'description': 'Your pictures, newest first, each with its id, size, '
                       'dimensions and whether it is published.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': {'type': 'integer', 'description': 'how many (default 50)'},
            'offset': {'type': 'integer', 'description': 'skip this many'},
            'public_only': {'type': 'boolean',
                            'description': 'only the published ones'}}},
        'handler': _t_images,
    },
    'store_public': {
        'description': 'Everything anyone on this box has published — the open '
                       'shelf. These have permanent uncredentialed URLs.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': {'type': 'integer', 'description': 'how many (default 50)'},
            'offset': {'type': 'integer', 'description': 'skip this many'}}},
        'handler': _t_public,
    },
    'store_image': {
        'description': 'One picture\'s record — yours, or anyone\'s published '
                       'one. An unpublished id you do not own answers 404 '
                       'exactly like an id that never existed.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': ID_DESC}},
            'required': ['id']},
        'handler': _t_image,
    },
    'store_add': {
        'description': 'Store an image — either a `path` on this box or raw '
                       '`base64` bytes. The format is decided by sniffing the '
                       'bytes: png, jpeg, gif, webp and bmp only, and SVG is '
                       'refused because it can carry script. Storing does NOT '
                       'share: it stays private until you publish it or mint a '
                       'code for it. Re-adding the same bytes is a no-op.',
        'inputSchema': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'a file on this machine'},
            'base64': {'type': 'string', 'description': 'the image bytes, '
                                                        'base64-encoded'},
            'name': {'type': 'string', 'description': 'display name (optional)'},
            'public': {'type': 'boolean', 'description':
                       'publish it immediately — permanent and open '
                       '(default false)'}}},
        'handler': _t_add,
    },
    'store_publish': {
        'description': 'Give a picture a permanent public URL. No credential, '
                       'no expiry, no audience — anyone who ever sees the link '
                       'sees the picture. Not undoable for anyone who already '
                       'has the link and kept the bytes. If the picture is for '
                       'one person, use store_grant instead.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': ID_DESC}},
            'required': ['id']},
        'handler': _t_publish,
    },
    'store_unpublish': {
        'description': 'Take the public URL away. Copies already made stay '
                       'made — this stops new readers, it does not recall '
                       'anything.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': ID_DESC}},
            'required': ['id']},
        'handler': _t_unpublish,
    },
    'store_grant': {
        'description': 'Mint a one-time code for a picture you own: good for '
                       'exactly one fetch and exactly N seconds, whichever '
                       'ends first. Returns `page_url` — a page that explains '
                       'the code, counts down and carries the button that '
                       'spends it — which is the link to hand over or render '
                       'as a QR code. `bytes_url` is the raw image and is '
                       'burned by whatever fetches it first, so do not paste '
                       'that one anywhere a preview bot can see it.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': ID_DESC},
            'ttl_seconds': {'type': 'string', 'description': TTL_DESC},
            'qr_ascii': {'type': 'boolean', 'description':
                         'also render the QR code as terminal text'}},
            'required': ['id']},
        'handler': _t_grant,
    },
    'store_grants': {
        'description': 'Codes you have minted that are still live, with how '
                       'many seconds each has left. Pass all=true to include '
                       'the spent and expired ones.',
        'inputSchema': {'type': 'object', 'properties': {
            'all': {'type': 'boolean', 'description':
                    'include spent and expired codes'},
            'limit': {'type': 'integer', 'description': 'how many (default 50)'}}},
        'handler': _t_grants,
    },
    'store_peek': {
        'description': 'Is this code still good? Asking does NOT spend it — '
                       'this is the safe way to check a link before or after '
                       'handing it over. Tells you live / already used / '
                       'expired, and never reveals which picture is behind it.',
        'inputSchema': {'type': 'object', 'properties': {
            'code': {'type': 'string', 'description': CODE_DESC}},
            'required': ['code']},
        'handler': _t_peek,
    },
    'store_claim': {
        'description': 'Redeem a code. THIS SPENDS IT — the next fetch of the '
                       'same code gets 410, including yours. Only call it when '
                       'the intent is to receive the picture; to check whether '
                       'a code still works, call store_peek. Pass out=<path> '
                       'to write the bytes somewhere, because they are not '
                       'retrievable afterwards.',
        'inputSchema': {'type': 'object', 'properties': {
            'code': {'type': 'string', 'description': CODE_DESC},
            'out': {'type': 'string', 'description':
                    'file path to write the picture to'}},
            'required': ['code']},
        'handler': _t_claim,
    },
    'store_revoke': {
        'description': 'Kill a live code before anyone spends it — the screen '
                       'showing the QR walked out of the room.',
        'inputSchema': {'type': 'object', 'properties': {
            'code': {'type': 'string', 'description': CODE_DESC}},
            'required': ['code']},
        'handler': _t_revoke,
    },
    'store_delete': {
        'description': 'Delete your row for a picture, every code pointing at '
                       'it, and the bytes themselves if no one else holds a '
                       'copy of the same image.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': ID_DESC}},
            'required': ['id']},
        'handler': _t_delete,
    },
    'store_share': {
        'description': 'The one-call version of the usual errand: take a '
                       'picture — a file path, base64 bytes, or something '
                       'already in the library — store it if it is new, and '
                       'mint a one-time code for it. The picture stays '
                       'private; the code is the only way in and it dies on '
                       'first use or on the clock. This is what to reach for '
                       'when somebody says "send this to X" — store_publish '
                       'is the other thing entirely and is permanent.',
        'inputSchema': {'type': 'object', 'properties': {
            'path': {'type': 'string', 'description': 'a file on this machine'},
            'base64': {'type': 'string', 'description': 'the image bytes, '
                                                        'base64-encoded'},
            'id': {'type': 'string', 'description':
                   'or something already stored — ' + ID_DESC},
            'name': {'type': 'string', 'description': 'display name (optional)'},
            'ttl_seconds': {'type': 'string', 'description': TTL_DESC},
            'qr_ascii': {'type': 'boolean', 'description':
                         'also render the QR as terminal text'}}},
        'handler': _t_share,
    },
    'store_view': {
        'description': 'Look at a picture — returns the image itself, not a '
                       'description of it. Works on anything you own and on '
                       'anything published here. Use it to check you are '
                       'about to share the right thing, since publishing is '
                       'permanent and a code cannot be un-spent.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': ID_DESC}},
            'required': ['id']},
        'handler': _t_view,
    },
    'store_qr': {
        'description': 'Render a QR code for a grant you already minted, as an '
                       'SVG image. It encodes the page link, and drawing it '
                       'spends nothing — the code is only burned when someone '
                       'presses the button on the page it opens.',
        'inputSchema': {'type': 'object', 'properties': {
            'code': {'type': 'string', 'description': CODE_DESC},
            'scale': {'type': 'integer', 'description':
                      'pixels per module (default 6)'}},
            'required': ['code']},
        'handler': _t_qr,
    },
    'store_health': {
        'description': 'Whether the index still agrees with the disk: counts, '
                       'live codes, whether a QR encoder is installed, and any '
                       'published record whose bytes have gone missing.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_health,
    },
}


def tool_list():
    """The schema half, without the handlers — what /mcp/schema serves."""
    return [{'name': name, 'description': tool['description'],
             'inputSchema': tool['inputSchema']}
            for name, tool in TOOLS.items()]


def version() -> str:
    try:
        return json.loads((MODULE_DIR / 'config.json').read_text()).get(
            'version') or '0.0.0'
    except Exception:
        return '0.0.0'


def schema() -> dict:
    """Everything a client needs to wire this up, in one document."""
    api = os.environ.get('STORE_SHARE_API',
                         f'http://127.0.0.1:'
                         f'{os.environ.get("STORE_SHARE_PORT", 50670)}')
    return {
        'server': {'name': 'store', 'version': version()},
        'protocolVersion': DEFAULT_PROTOCOL_VERSION,
        'supportedProtocolVersions': list(SUPPORTED_PROTOCOL_VERSIONS),
        'instructions': INSTRUCTIONS,
        'transports': {
            'http': {'url': f'{api.rstrip("/")}/mcp',
                     'same_origin': f'{links.BASE}/mcp',
                     'method': 'POST',
                     'note': 'one JSON-RPC 2.0 message per request'},
            'stdio': {'command': sys.executable,
                      'args': [str(MODULE_DIR / 'src' / 'mcp.py')]},
        },
        'client_config': {
            'mcpServers': {
                'store': {'command': sys.executable,
                          'args': [str(MODULE_DIR / 'src' / 'mcp.py')]}}},
        'capabilities': CAPABILITIES,
        'tools': tool_list(),
        'resources': {'templates': RESOURCE_TEMPLATES,
                      'note': 'store://docs, store://docs/<section> and '
                              'store://image/<id> — call resources/list for '
                              'the concrete ones, which depend on who is '
                              'asking'},
        'prompts': _prompt_list(),
    }


# ── resources ────────────────────────────────────────────────────────
#
# Tools are verbs and resources are nouns, and a client that lets a person
# attach context by hand wants the nouns. The documentation is one, and so is
# every picture the caller owns — which is the only way a model gets to look at
# a stored image without a tool call spending a turn.

DOC_SECTIONS = ('sharing', 'endpoints', 'cli', 'env', 'safety', 'mcp')


def _resources(owner):
    from src import docs
    out = [{'uri': 'store://docs',
            'name': 'store documentation',
            'description': 'The whole manual as JSON: the two ways of '
                           'sharing, every endpoint, the CLI, the environment '
                           'and what this refuses to store.',
            'mimeType': 'application/json'}]
    out += [{'uri': f'store://docs/{section}',
             'name': f'store documentation — {section}',
             'description': f'The {section} section on its own.',
             'mimeType': 'application/json'}
            for section in DOC_SECTIONS]
    for row in library.listing(owner, limit=100):
        out.append({
            'uri': f'store://image/{row["id"]}',
            'name': row['name'],
            'description': f'{row["size"]} bytes'
                           + (f' · {row["width"]}x{row["height"]}'
                              if row['width'] else '')
                           + (' · published' if row['public'] else ' · private'),
            'mimeType': row['mime'],
            'size': row['size']})
    return out


def _read_resource(uri, owner):
    from src import docs
    uri = str(uri or '')
    if uri == 'store://docs':
        return [{'uri': uri, 'mimeType': 'application/json',
                 'text': json.dumps(docs.document(), indent=2, default=str)}]
    if uri.startswith('store://docs/'):
        section = uri[len('store://docs/'):]
        return [{'uri': uri, 'mimeType': 'application/json',
                 'text': json.dumps(docs.document(section), indent=2,
                                    default=str)}]
    if uri.startswith('store://image/'):
        image_id = resolve.image(uri[len('store://image/'):], owner,
                                 public_too=True)
        record = (library.record(image_id, owner)
                  or library.public_record(image_id))
        if record is None:
            raise StoreError(f'no such image: {uri}', 404)
        data = library.read(image_id)
        if len(data) > MAX_INLINE_BYTES:
            raise StoreError(
                f'{record["name"]} is {len(data)} bytes, past the '
                f'{MAX_INLINE_BYTES} inline ceiling', 413)
        # `blob`, not `text`: these are bytes, and a client that renders images
        # is looking for the base64 field rather than a description of one.
        return [{'uri': uri, 'mimeType': record['mime'],
                 'blob': base64.b64encode(data).decode('ascii')}]
    raise StoreError(
        f'unknown resource: {uri} — try store://docs or store://image/<id>',
        404)


RESOURCE_TEMPLATES = [
    {'uriTemplate': 'store://image/{id}',
     'name': 'a stored picture',
     'description': 'The bytes of one picture you own, or one published here. '
                    '`id` may be shortened to any unique prefix, or be the '
                    'name it was stored under.',
     'mimeType': 'image/*'},
    {'uriTemplate': 'store://docs/{section}',
     'name': 'one section of the manual',
     'description': 'One of: ' + ', '.join(DOC_SECTIONS),
     'mimeType': 'application/json'},
]


# ── prompts ──────────────────────────────────────────────────────────
#
# Two, because there are two ways to share and choosing wrongly is the mistake
# this module exists to make hard. A prompt is where a client puts the
# sentence a person would otherwise have to phrase correctly themselves.

PROMPTS = {
    'share_with_one_person': {
        'description': 'Hand a picture to a specific person: store it, mint a '
                       'code that dies on first use, show the QR.',
        'arguments': [
            {'name': 'picture', 'description': 'a file path, or the name of '
                                               'something already stored',
             'required': True},
            {'name': 'how_long', 'description': 'how long the code should live '
                                                '(default 5m)',
             'required': False}],
        'template': (
            'Share {picture} with one person using this store.\n\n'
            'Use store_share with a ttl_seconds of {how_long}. Do NOT use '
            'store_publish — that would give the picture a permanent public '
            'URL, which is not what was asked for and cannot be undone for '
            'copies already made.\n\n'
            'Report back the page_url (the link that is safe to send, because '
            'opening it claims nothing) and how long it is good for. Do not '
            'send the bytes_url anywhere: whatever fetches it first spends '
            'the code.'),
    },
    'publish_forever': {
        'description': 'Give a picture a permanent public URL — deliberately, '
                       'having checked it is the right picture.',
        'arguments': [
            {'name': 'picture', 'description': 'a file path, or the name of '
                                               'something already stored',
             'required': True}],
        'template': (
            'Publish {picture} from this store as a permanent public link.\n\n'
            'First call store_view on it and confirm out loud that it is the '
            'picture that was meant — publishing has no credential, no expiry '
            'and no audience, and unpublishing does not recall copies anyone '
            'already made.\n\n'
            'Then call store_publish and report the page_url.'),
    },
}


def _prompt_list():
    return [{'name': name, 'description': prompt['description'],
             'arguments': prompt['arguments']}
            for name, prompt in PROMPTS.items()]


def _prompt_get(name, arguments):
    prompt = PROMPTS.get(str(name or ''))
    if not prompt:
        raise StoreError(
            f'no such prompt: {name} — try {", ".join(PROMPTS)}', 404)
    arguments = arguments if isinstance(arguments, dict) else {}
    filled = prompt['template']
    for argument in prompt['arguments']:
        key = argument['name']
        value = arguments.get(key)
        if value in (None, '') and argument.get('required'):
            raise StoreError(f'{key} is required for this prompt', 400)
        filled = filled.replace('{' + key + '}',
                                str(value) if value not in (None, '')
                                else '5m' if key == 'how_long' else '')
    return {'description': prompt['description'],
            'messages': [{'role': 'user',
                          'content': {'type': 'text', 'text': filled}}]}


CAPABILITIES = {
    'tools': {'listChanged': False},
    'resources': {'subscribe': False, 'listChanged': False},
    'prompts': {'listChanged': False},
}


# ── JSON-RPC 2.0 ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code,
                                                   'message': message}}


def _call_tool(id_, params, owner):
    name = str(params.get('name') or '')
    tool = TOOLS.get(name)
    if not tool:
        return _error(id_, -32602, f'unknown tool: {name}')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')
    try:
        result = tool['handler'](args, _owner(owner))
    except StoreError as error:
        # A refusal is a successful JSON-RPC response carrying isError, per the
        # spec, so the model reads why and can correct itself rather than
        # seeing transport noise.
        return _result(id_, {'content': [{'type': 'text', 'text': str(error)}],
                             'isError': True})
    except Exception as error:
        return _result(id_, {'content': [
            {'type': 'text',
             'text': f'{name} failed: {type(error).__name__}: {error}'}],
            'isError': True})
    # A handler that already knows how it wants to be seen — one returning an
    # actual picture — hands back its own content blocks. Everything else is
    # JSON, and gets wrapped the one way JSON is worth showing.
    if isinstance(result, dict) and isinstance(result.get('content'), list):
        out = {'content': result['content'], 'isError': False}
        if isinstance(result.get('structuredContent'), dict):
            out['structuredContent'] = result['structuredContent']
        return _result(id_, out)

    text = result if isinstance(result, str) else json.dumps(result, indent=2,
                                                             default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _result(id_, out)


def handle(body, owner=None):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600,
                      'invalid request: expected a JSON-RPC 2.0 object with a '
                      'method')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        asked = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': asked if asked in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': CAPABILITIES,
            'serverInfo': {'name': 'store', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call_tool(id_, params, owner)
    if method == 'resources/list':
        return _result(id_, {'resources': _resources(_owner(owner))})
    if method == 'resources/templates/list':
        return _result(id_, {'resourceTemplates': RESOURCE_TEMPLATES})
    if method == 'resources/read':
        try:
            contents = _read_resource(params.get('uri'), _owner(owner))
        except StoreError as error:
            # Unlike a tool call, a resource read has no isError channel — a
            # client asking for a URI that is not there wants a JSON-RPC error.
            return _error(id_, -32002, str(error))
        return _result(id_, {'contents': contents})
    if method == 'prompts/list':
        return _result(id_, {'prompts': _prompt_list()})
    if method == 'prompts/get':
        try:
            return _result(id_, _prompt_get(params.get('name'),
                                            params.get('arguments')))
        except StoreError as error:
            return _error(id_, -32602, str(error))
    if method == 'completion/complete':
        return _result(id_, {'completion': {'values': [], 'hasMore': False}})
    return _error(id_, -32601, f'method not found: {method}')


def handle_message(body, owner=None):
    """
    One message or a batch of them — the shape a JSON-RPC 2.0 peer may send.

    Arrays are part of the base spec and MCP clients before 2025-06-18 do send
    them, usually an `initialize` bundled with the `notifications/initialized`
    that follows it. Handling only objects made this server look broken to
    exactly the clients most likely to try it. A batch of nothing but
    notifications gets no reply at all, which is also the spec.
    """
    if isinstance(body, list):
        if not body:
            return _error(None, -32600, 'empty batch')
        replies = [r for r in (handle(one, owner) for one in body)
                   if r is not None]
        return replies or None
    return handle(body, owner)


# ── stdio transport ──────────────────────────────────────────────────

def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            response = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            response = handle_message(body)
        if response is not None:
            sys.stdout.write(json.dumps(response, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    if '--schema' in sys.argv[1:]:
        print(json.dumps(schema(), indent=2))
    else:
        serve_stdio()
