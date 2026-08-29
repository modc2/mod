"""
The manual, as data.

The console's DOCS tab, the API's `/docs` endpoint and the MCP `store_docs`
tool are three renderings of this one dictionary, so a route that changes is
wrong in one place instead of right in one and stale in two. It is data rather
than prose because two of those three readers are programs.

The README is the long version and this is the reference: what the URLs are,
what the CLI verbs are, what the environment variables do, and — the part
worth reading before anything else — which of the two ways of sharing a
picture you actually meant.
"""
from . import grants, library, links, mcp, qr, resolve


def sharing():
    return {
        'title': 'Two ways to hand somebody a picture',
        'summary': 'They are deliberately not the same thing. Pick by asking '
                   'who the audience is: everyone forever, or one person now.',
        'ways': [
            {'name': 'publish',
             'audience': 'anyone who ever sees the link, for as long as it is there',
             'page': '/p/<id>',
             'bytes': '/i/<id>',
             'credential': 'none',
             'expiry': 'none',
             'note': 'Unpublishing stops new readers. It does not recall the '
                     'copies already made.'},
            {'name': 'grant',
             'audience': 'the first person to open it, within N seconds',
             'page': '/v/<code>',
             'bytes': '/g/<code>',
             'credential': 'the code itself — whoever holds it is the audience',
             'expiry': f'{grants.MIN_TTL}–{grants.MAX_TTL} seconds, '
                       f'{grants.DEFAULT_TTL} by default',
             'note': 'One fetch or the clock, whichever comes first. Never '
                     'scanning a code is exactly as safe as scanning it once.'},
        ],
        'page_vs_bytes': 'The page is what goes in the QR code and what you '
                         'hand to a person: it says what the thing is, counts '
                         'the timer down, and carries the button that spends '
                         'the code. Opening the page claims nothing, so a chat '
                         'preview or a prefetch cannot burn a grant any more — '
                         'only a human pressing the button does. The bytes '
                         'route still exists and still burns on fetch, which '
                         'is what a script wants.',
        'one_time_mechanism': 'A claim is one conditional UPDATE that the '
                              'database adjudicates (claimed IS NULL AND '
                              'expires > now, rowcount decides), not a read '
                              'followed by a write — two phones scanning the '
                              'same on-screen code at once is the ordinary '
                              'case, and read-then-write hands the picture to '
                              'both.',
        'spent_codes': 'A burned code answers 410, not 404, so the holder can '
                       'tell "someone beat you to it" from "you typed it '
                       'wrong".',
    }


def endpoints():
    return [
        {'method': 'GET', 'path': '/', 'what': 'what this is, and its numbers'},
        {'method': 'GET', 'path': '/health', 'what': 'is the index telling the '
                                                     'truth about the disk'},
        {'method': 'GET', 'path': '/docs', 'what': 'this document — a page '
         'for a browser, JSON for anything that asks for JSON. ?section= for '
         'one part, ?format=html|json to override the negotiation.'},
        {'method': 'GET', 'path': '/me', 'what': 'who you are to this store'},
        {'method': 'GET', 'path': '/images', 'what': 'your pictures',
         'auth': 'yours'},
        {'method': 'GET', 'path': '/public', 'what': 'everything published here'},
        {'method': 'GET', 'path': '/image?id=', 'what': 'one record'},
        {'method': 'POST', 'path': '/upload?name=&public=',
         'what': 'raw image bytes as the body — no multipart', 'auth': 'yours'},
        {'method': 'POST', 'path': '/publish {id}', 'what': 'give it a public URL',
         'auth': 'yours'},
        {'method': 'POST', 'path': '/unpublish {id}', 'what': 'take it away',
         'auth': 'yours'},
        {'method': 'DELETE', 'path': '/image?id=', 'what': 'delete it',
         'auth': 'yours'},
        {'method': 'GET', 'path': '/p/{id}',
         'what': 'PAGE for a published image — hand this to a person'},
        {'method': 'GET', 'path': '/i/{id}',
         'what': 'the published bytes. Open, cacheable, no credential.'},
        {'method': 'GET', 'path': '/b/{id}',
         'what': 'the bytes of a picture YOU own, published or not — the one '
                 'read path that checks who is asking', 'auth': 'yours'},
        {'method': 'POST', 'path': '/grant {id, ttl_seconds}',
         'what': 'mint a one-time code', 'auth': 'yours'},
        {'method': 'GET', 'path': '/v/{code}',
         'what': 'PAGE for a one-time code. Opening it claims NOTHING.'},
        {'method': 'GET', 'path': '/g/{code}',
         'what': 'the bytes behind the code. THIS BURNS IT.'},
        {'method': 'HEAD', 'path': '/g/{code}',
         'what': 'is the code live — never claims'},
        {'method': 'GET', 'path': '/g/{code}/qr',
         'what': 'a QR picture of the page link — spends nothing'},
        {'method': 'GET', 'path': '/grant?code=',
         'what': 'peek: would it work? Asking does not spend it.'},
        {'method': 'GET', 'path': '/grants', 'what': 'your live codes',
         'auth': 'yours'},
        {'method': 'DELETE', 'path': '/grant?code=', 'what': 'revoke a live code',
         'auth': 'yours'},
        {'method': 'POST', 'path': '/sweep', 'what': 'forget grants that stopped '
                                                     'mattering'},
        {'method': 'GET', 'path': '/qr?text=', 'what': 'a QR code for anything'},
        {'method': 'POST', 'path': '/mcp', 'what': 'MCP, JSON-RPC 2.0, one '
                                                   'message per request'},
        {'method': 'GET', 'path': '/mcp/schema', 'what': 'the MCP tool list, '
                                                         'prompts, resource '
                                                         'templates and client '
                                                         'config'},
        {'method': 'DELETE', 'path': '/mcp', 'what': 'a client ending its '
                                                     'session — there is no '
                                                     'session state, and '
                                                     'saying so beats a 405'},
    ]


def naming():
    """The part that makes the rest of it usable by hand."""
    return {
        'title': 'You never have to type a sha256',
        'a_picture': [
            {'form': 'the full id', 'example': 'e54c50db…(64 hex)',
             'note': 'the sha256 of its bytes — what the database keys on'},
            {'form': 'any unique prefix of it', 'example': 'e54c',
             'note': f'at least {resolve.MIN_PREFIX} characters'},
            {'form': 'the name you stored it under', 'example': 'sunset.png',
             'note': 'an id prefix wins if both could match'},
            {'form': 'the newest one', 'example': 'latest',
             'note': '`last` and `newest` mean the same thing'},
        ],
        'ambiguity': 'Two matches is an error that names both, never a guess. '
                     'The verbs on the other end of this publish things '
                     'forever and delete things permanently, and a resolver '
                     'that picks a winner turns a typo into whichever of those '
                     'you happened to be running.',
        'a_duration': [
            {'form': 'seconds', 'example': '90'},
            {'form': 'with a unit', 'example': '30s · 5m · 2h · 1d'},
        ],
        'duration_bounds': f'{grants.MIN_TTL} second to '
                           f'{resolve.human_duration(grants.MAX_TTL)}, '
                           f'{grants.DEFAULT_TTL}s by default',
        'a_code': 'The whole code, as it was handed to you. A prefix works '
                  'too, but only for codes you minted yourself and only from '
                  'the CLI or an MCP tool — the public claim and peek routes '
                  'take the whole thing and nothing shorter, because a public '
                  'endpoint that accepts prefixes can be walked.',
    }


def cli():
    return [
        ('python3 mod.py', 'what is on the shelf'),
        ('python3 mod.py help', 'every verb, in one screen'),
        ('python3 mod.py share photo.jpg for=5m',
         'store it AND mint a code AND draw the QR — the usual errand'),
        ('python3 mod.py add photo.jpg', 'store it, private'),
        ('python3 mod.py add photo.jpg public=True', 'store it, published'),
        ('python3 mod.py images', 'yours'),
        ('python3 mod.py publish sunset.png', 'give it a public URL'),
        ('python3 mod.py unpublish sunset.png', 'take the public URL away'),
        ('python3 mod.py grant latest for=30s', 'a code good for 30 seconds'),
        ('python3 mod.py qr e54c for=2m', 'the same, printed as a QR'),
        ('python3 mod.py grants', 'codes still live'),
        ('python3 mod.py peek <code>', 'would it work? does not spend it'),
        ('python3 mod.py claim <code> out=/tmp/got.png', 'redeem it — burns it'),
        ('python3 mod.py revoke <code>', 'kill a live code'),
        ('python3 mod.py rm sunset.png', 'delete a picture'),
        ('python3 mod.py docs', 'this document'),
        ('python3 mod.py mcp', 'the MCP tool list'),
        ('python3 mod.py serve', 'api :50670, console :50671'),
    ]


def env():
    return [
        ('STORE_SHARE_HOME', 'state directory', str(library.HOME)),
        ('STORE_SHARE_PORT', 'API port', '50670'),
        ('STORE_SHARE_APP_PORT', 'console port', '50671'),
        ('STORE_SHARE_HOST', 'API bind — read identity.py before changing',
         '127.0.0.1'),
        ('STORE_SHARE_BASE', 'the origin share links are built from', links.BASE),
        ('STORE_SHARE_MAX_BYTES', 'upload ceiling', str(library.MAX_BYTES)),
        ('STORE_SHARE_SESSION_TTL', 'how long a signed token stays good', '7d'),
    ]


def safety():
    return {
        'formats': 'png, jpeg, gif, webp, bmp — decided by magic bytes, never '
                   'by the filename or the Content-Type the uploader claims',
        'svg': 'refused. Every other image format is inert data; SVG is a '
               'document that can carry script, and this is served from an '
               'origin shared with the whole fleet.',
        'headers': "every image goes out under Content-Security-Policy: "
                   "default-src 'none'; sandbox, X-Content-Type-Options: "
                   'nosniff and an inline disposition',
        'grant_responses': 'no-store — nothing between here and the phone keeps '
                           'a copy of something that was good for one fetch',
        'private_probing': 'an unpublished id and an id that never existed both '
                           'answer 404',
        'identity': 'the fleet\'s shared protocol auth (EIP-191 over secp256k1). '
                    'Over loopback an unauthenticated caller is the local '
                    'owner, which is why the port binds to 127.0.0.1.',
        'residual_risk': 'the code is still a credential in a URL. The page '
                         'means an automated fetch no longer spends it, but '
                         'anyone the link reaches can press the button — mint '
                         'short codes, and mint them when the person is there.',
    }


def document(section: str = ''):
    """Everything, or one section of it."""
    whole = {
        'name': 'store',
        'what': 'image sharing — permanent public links, and one-time codes '
                'that expire after N seconds',
        'not_a_module': 'orbit/store is shadowed by core/store: m.mod("store") '
                        'is NOT this. It works over HTTP on its own ports and '
                        'through `python3 mod.py <fn>`.',
        'share_base': links.BASE,
        'state': str(library.HOME),
        'sharing': sharing(),
        'naming': naming(),
        'endpoints': endpoints(),
        'cli': [{'command': c, 'what': w} for c, w in cli()],
        'env': [{'name': n, 'what': w, 'value': v} for n, w, v in env()],
        'safety': safety(),
        'mcp': {'instructions': mcp.INSTRUCTIONS,
                'schema_url': f'{links.BASE}/mcp/schema',
                'http': f'{links.BASE}/mcp',
                'stdio': 'python3 src/mcp.py',
                'protocol_versions': list(mcp.SUPPORTED_PROTOCOL_VERSIONS),
                'capabilities': mcp.CAPABILITIES,
                'client_config': mcp.schema()['client_config'],
                'tools': mcp.tool_list(),
                'resources': mcp.RESOURCE_TEMPLATES,
                'prompts': mcp._prompt_list(),
                'batching': 'a JSON-RPC array is answered with an array of '
                            'the replies that have ids; a batch of nothing '
                            'but notifications is answered with 202 and no '
                            'body',
                'pictures_come_back_as_pictures': 'store_view, store_qr and '
                    'store_claim return MCP image content blocks, not a '
                    'sentence about an image — claim especially, because the '
                    'code is spent by then and that response is the only copy '
                    'anyone gets'},
        'limits': {'max_bytes': library.MAX_BYTES,
                   'ttl_seconds': {'min': grants.MIN_TTL, 'max': grants.MAX_TTL,
                                   'default': grants.DEFAULT_TTL},
                   'qr_encoder': qr.available()},
    }
    if section:
        if section not in whole:
            raise library.StoreError(
                f'no such section: {section} — try one of '
                f'{", ".join(k for k in whole if isinstance(whole[k], (dict, list)))}',
                404)
        return {section: whole[section]}
    return whole
