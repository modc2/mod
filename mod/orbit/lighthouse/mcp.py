#!/usr/bin/env python3
"""lighthouse mcp — the module's two halves, as tools an agent can hold.

Fourteen tools over the same code the CLI and the API use, so a model gets the
whole shape of this module: bytes go to lighthouse.storage and land on
IPFS/Filecoin with a perpetual pin, and the CID is then *registered* in the
**store** module, which is where visibility, grants, pools and the marketplace
live. Nothing is copied into the store — it keeps the gateway url.

Two secrets travel with a call, and they are not the same thing:

    protocol token   `{data, time, key, signature}` — who you are to the fleet
                     and to the store. Forwarded verbatim; this server never
                     signs for anybody.
    Lighthouse key   what pays for the pin. Per-call, or the deployment's.

Two transports, and they are deliberately not equal:

    stdio    python3 mcp.py — a local process holding the box's own keys, so
             it may touch the filesystem (`path`, `out`) and persist the
             deployment key.
    http     POST /mcp on the API — a remote caller. Filesystem tools are
             refused with a message saying which route to use instead, and
             anything that writes needs a signed token.

Self-contained JSON-RPC 2.0 on the stdlib — no `mcp` package.

    python3 mcp.py                      # stdio
    python3 mcp.py --tools              # print the schema and exit
    curl -s localhost:50680/mcp | jq    # the same schema, over HTTP

api/api.py mounts `handle()` at POST /mcp and serves `describe()` at GET /mcp,
so the tools, the REST routes and the console can never drift apart.
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    # Appended, not prepended: this directory holds a mod.py that would shadow
    # the protocol's own `mod` package for anything importing us (protocol.py).
    sys.path.append(str(HERE))

from store_link import StoreError, StoreLink  # noqa: E402
import store_link  # noqa: E402

CONFIG = json.loads((HERE / 'config.json').read_text())
SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-06-18'

INSTRUCTIONS = (
    'Perpetual storage with a door onto the store module. lighthouse_put sends '
    'bytes to lighthouse.storage — one payment, an IPFS CID, a Filecoin deal, '
    'pinned forever — and by default registers that CID in the store module, '
    'which is where visibility, timed grants, data pools and the marketplace '
    'live; the bytes never move into the store, it keeps the gateway url and '
    'redirects readers back. Start with lighthouse_status: it says whether a '
    'Lighthouse key is configured and whether the store will accept this caller '
    '(`store.blockers` names what is in the way, `store.can_push` is the verdict '
    '— check it BEFORE uploading rather than reading a failure afterwards). '
    'lighthouse_mirror makes something the store already holds perpetual. '
    'Every store call forwards the CALLER\'S OWN protocol token, so the store\'s '
    'whitelist, terms and quota apply to whoever signed — this server holds no '
    'store credential and cannot get you past a gate you could not pass '
    'directly. Uploads spend the Lighthouse key: your own via the `key` argument '
    '(never written to disk) or the deployment\'s. Two things that surprise '
    'people: lighthouse_forget does NOT unpin (perpetual is perpetual, the CID '
    'stays retrievable), and a failed store registration never costs you the '
    'CID — the upload happened first and the outcome is reported under `store`.'
)


def _core():
    """This module's own mod.py, by path — `import mod` is the protocol."""
    spec = importlib.util.spec_from_file_location('lighthouse_core', HERE / 'mod.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CORE = _core()
STORE = StoreLink()


class Refused(Exception):
    """A tool that will not run, with the reason a model can act on."""


# ── who is calling ───────────────────────────────────────────────────

class Ctx:
    """The caller, their two secrets, and how much of the box they may touch.

    `local` is the whole security story of this file. A stdio server is a
    process someone started on the box with the box's own keys, so it may read
    and write local paths and persist the deployment key. An HTTP caller is
    not, and the tools that touch the filesystem refuse rather than pretending
    the paths mean anything to them.
    """

    def __init__(self, token=None, key=None, local=True):
        self._token = (token or '').strip() or None
        if self._token and self._token.lower().startswith('bearer '):
            self._token = self._token[7:].strip()
        self.key = (key or '').strip() or None
        self.local = bool(local)

    def with_key(self, key):
        """The same caller, spending a key they passed on this one call."""
        if not (key or '').strip():
            return self
        return Ctx(token=self._token, key=key, local=self.local)

    # the Lighthouse key
    def lh(self):
        return CORE.Mod(api_key=self.key)

    def key_source(self) -> str:
        if self.key:
            return 'call'
        return 'deployment' if CORE.Mod().api_key else 'none'

    def paying_key(self):
        """The client for an upload, or a refusal that says how to get a key."""
        lh = self.lh()
        if not lh.api_key:
            raise Refused('no Lighthouse API key: pass `key` on this call, or '
                          'set the deployment key with lighthouse_set_key '
                          '(get one at https://files.lighthouse.storage)')
        return lh

    # the protocol token
    def token(self) -> str:
        if self._token:
            return self._token
        if not self.local:
            raise Refused('the store needs your protocol token — send it as '
                          '`Authorization: Bearer <token>` on the MCP request '
                          '(mint one with m.mod("auth")().token({}))')
        return store_link.local_token()          # this box's own key: stdio only

    def maybe_token(self):
        try:
            return self.token()
        except Refused:
            return None

    def address(self):
        """Who an upload is recorded under."""
        if self._token:
            try:
                import identity
                return identity.from_token(self._token)
            except Exception:
                return None
        return store_link.local_address() if self.local else None

    def filesystem(self, what: str):
        if not self.local:
            raise Refused(
                f'{what} names a path on the server\'s filesystem, which an HTTP '
                'caller does not share — this tool is stdio only. Over HTTP use '
                '`text` instead of `path` for lighthouse_put, lighthouse_preview '
                'to read a CID, or the REST routes POST /put and GET /get.')


LOCAL_CTX = Ctx()          # stdio default: the box's own keys


# ── tools ────────────────────────────────────────────────────────────

def _t_status(a, ctx):
    lh = ctx.lh()
    out = lh.status()
    out.update({
        'version': CONFIG.get('version'),
        'key_source': ctx.key_source(),
        'perpetual': True,
        'store': STORE.status(ctx.maybe_token()),
    })
    return out


def _register(result, ctx, public, pool):
    """Attach the store outcome to an upload without ever failing the upload.

    The pin is the irreversible half and it already happened; a store that is
    down, or a caller the store will not accept, is reported here and the caller
    keeps their CID either way.
    """
    try:
        token = ctx.token()
    except Refused as e:
        return {'registered': False, 'error': str(e)}
    try:
        reg = STORE.register(token, cid=result['cid'], key=result.get('key'),
                             size=result.get('size'), url=result.get('url'),
                             public=bool(public), pool=pool)
        return {'registered': True, **reg}
    except StoreError as e:
        return {'registered': False, 'error': e.message, 'status': e.status}


def _t_put(a, ctx):
    path, text = a.get('path'), a.get('text')
    if (path is None) == (text is None):
        raise Refused('give exactly one of `path` (a file on the box, stdio only) '
                      'or `text` (a string, any transport)')
    # The transport question is settled before the key question: a caller who
    # cannot name a path here should hear that, not be sent for a key first.
    if path is not None:
        ctx.filesystem('`path`')
    lh = ctx.paying_key()
    if path is not None:
        result = lh.put(path=path, owner=ctx.address(), key=a.get('key'))
    else:
        name = a.get('key') or 'note.txt'
        tmp = Path(tempfile.mkdtemp(prefix='lighthouse-mcp-')) / Path(str(name)).name
        try:
            tmp.write_text(str(text))
            result = lh.put(path=str(tmp), owner=ctx.address(), key=str(name))
        finally:
            tmp.unlink(missing_ok=True)
            try:
                tmp.parent.rmdir()
            except OSError:
                pass
    if a.get('register', True):
        result['store'] = _register(result, ctx, a.get('public'), a.get('pool'))
    else:
        result['store'] = {'registered': False,
                           'error': 'register=false — this CID is pinned but the '
                                    'store does not know about it; '
                                    'lighthouse_register adds it later'}
    return result


def _t_get(a, ctx):
    ctx.filesystem('`out`')
    return ctx.lh().get(cid=a['cid'], out=a.get('out'))


def _t_preview(a, ctx):
    return ctx.lh().preview(a['cid'], max_bytes=int(a.get('max_bytes') or 65536))


def _t_list(a, ctx):
    lh = ctx.lh()
    address = ctx.address()
    scope = a.get('scope') or 'mine'
    if scope == 'all' and not ctx.local:
        import identity
        if not identity.is_owner(address):
            raise Refused('scope=all is owner only')
    rows = lh.list(owner=None if scope == 'all' else address,
                   limit=int(a.get('limit') or 100))
    gateway = lh.gateway.rstrip('/')
    for row in rows:
        row['url'] = f"{gateway}/ipfs/{row['cid']}"
    return {'owner': address, 'scope': scope, 'count': len(rows), 'objects': rows}


def _t_pin(a, ctx):
    return ctx.lh().pin(cid=a['cid'], owner=ctx.address())


def _t_forget(a, ctx):
    cid = a['cid']
    lh = ctx.lh()
    address = ctx.address()
    if not ctx.local:
        mine = any(r['cid'] == cid for r in lh.list(owner=address, limit=100000))
        if not mine:
            import identity
            if not identity.is_owner(address):
                raise Refused(f'{cid} is not yours')
    out = lh.rm(cid)
    out['note'] = ('removed from this module\'s index only — the Lighthouse pin '
                   'is perpetual and the CID stays retrievable by anyone holding it')
    return out


def _t_account(a, ctx):
    lh = ctx.lh()
    if not lh.api_key:
        raise Refused('no Lighthouse API key to report on: pass `key`, or set the '
                      'deployment key with lighthouse_set_key')
    out = {'key_source': ctx.key_source(), 'usage': lh.usage()}
    if a.get('uploads', True):
        out['uploads'] = lh.uploads(page=int(a.get('page') or 1))
    return out


def _t_store(a, ctx):
    return STORE.status(ctx.maybe_token())


def _t_accept_terms(a, ctx):
    if not a.get('accept'):
        return STORE.terms(ctx.maybe_token())
    try:
        return STORE.accept_terms(ctx.token())
    except StoreError as e:
        raise Refused(f'store refused the acceptance ({e.status}): {e.message}')


def _t_register(a, ctx):
    lh = ctx.lh()
    url = f"{lh.gateway.rstrip('/')}/ipfs/{a['cid']}"
    try:
        return STORE.register(ctx.token(), cid=a['cid'], key=a.get('key'),
                              size=a.get('size'), url=url,
                              public=bool(a.get('public')), pool=a.get('pool'))
    except StoreError as e:
        raise Refused(f'store refused the registration ({e.status}): {e.message}')


def _t_objects(a, ctx):
    try:
        objs = STORE.objects(ctx.token(), limit=int(a.get('limit') or 200),
                             only_lighthouse=not a.get('all_backends'))
    except StoreError as e:
        raise Refused(f'store {e.status}: {e.message}')
    return {'count': len(objs), 'objects': objs,
            'filter': 'all backends' if a.get('all_backends') else 'lighthouse only'}


def _t_mirror(a, ctx):
    import shutil
    lh = ctx.paying_key()
    token = ctx.token()
    tmpdir = Path(tempfile.mkdtemp(prefix='lighthouse-mcp-mirror-'))
    try:
        info = {}
        try:
            info = STORE.object_info(token, a['cid']) or {}
        except StoreError:
            pass                       # /object is a nicety; a readable CID is not
        name = a.get('key') or info.get('key') or a['cid']
        try:
            local = STORE.fetch(token, a['cid'], tmpdir / Path(str(name)).name)
        except StoreError as e:
            raise Refused(f'store would not hand over {a["cid"]} ({e.status}): '
                          f'{e.message}')
        result = lh.put(path=str(local), owner=ctx.address(), key=str(name))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    result['source_cid'] = a['cid']
    result['same_cid'] = result['cid'] == a['cid']
    result['store'] = _register(result, ctx, a.get('public'), a.get('pool'))
    return result


def _t_set_key(a, ctx):
    ctx.filesystem('this tool')
    out = CORE.Mod().set_key(a['api_key'])
    if out.get('error'):
        raise Refused(out['error'])
    out['stored'] = 'off-chain in ~/.mod/lighthouse/credentials.json (0600) — '\
                    'never config.json'
    return out


# ── schemas ──────────────────────────────────────────────────────────

def _str(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


def _num(desc, **kw):
    return {'type': 'number', 'description': desc, **kw}


def _bool(desc):
    return {'type': 'boolean', 'description': desc}


_CID = _str('an IPFS CID — Qm… (v0) or bafy… (v1)')
_KEY = _str('your own Lighthouse API key for this call, sent instead of the '
            'deployment key and never written to disk (files.lighthouse.storage)')
_PUBLIC = _bool('list the object publicly in the store (default false — private, '
                'readable only through a store grant). It has no effect on the '
                'bytes: a CID is retrievable by anyone holding it either way')
_POOL = _str('store data-pool id to file the object under')

_READ = {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': True}
_WRITE = {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': True}

TOOLS = {
    'lighthouse_status': {
        'description': 'Start here. Whether a Lighthouse key is configured and '
                       'which one this call would spend, the gateway, how many '
                       'objects this module has indexed, and the store link: '
                       'reachable, your address, whitelisted, terms signed, quota '
                       'used, `blockers` (what is still in the way) and `can_push` '
                       '(the verdict). Reading can_push first is cheaper than '
                       'reading a failed upload.',
        'inputSchema': {'type': 'object', 'properties': {'key': _KEY}},
        'annotations': _READ,
        'auth': False,
        'key_arg': 'key',
        'handler': _t_status,
    },
    'lighthouse_put': {
        'description': 'Store bytes forever: upload to lighthouse.storage (IPFS + '
                       'a Filecoin deal, perpetual pin) and register the CID in '
                       'the store module. Give `text` for a string, or `path` for '
                       'a file on the box (stdio only). Returns the CID, the '
                       'gateway url and a `store` block saying whether the '
                       'registration landed — if it did not, the bytes ARE still '
                       'pinned and lighthouse_register can add them later.',
        'inputSchema': {'type': 'object', 'properties': {
            'text': _str('the content to store, as a string'),
            'path': _str('a file on the server\'s filesystem — stdio transport only'),
            'key': _str('name to store it under (defaults to the filename, or '
                        'note.txt for text)'),
            'register': _bool('register the CID in the store module (default true)'),
            'public': _PUBLIC,
            'pool': _POOL,
            'api_key': _KEY,
        }},
        'annotations': _WRITE,
        'key_arg': 'api_key',
        'handler': _t_put,
    },
    'lighthouse_preview': {
        'description': 'Peek at a CID through the gateway without downloading it: '
                       'decoded text when it is text, byte count, content type and '
                       'a truncated flag. Needs no token — an IPFS CID is public '
                       'bytes to whoever holds it, and access control is the '
                       "store's job, not this gateway's.",
        'inputSchema': {'type': 'object', 'properties': {
            'cid': _CID,
            'max_bytes': _num('how much to read (default 65536, max 1048576)'),
        }, 'required': ['cid']},
        'annotations': _READ,
        'auth': False,
        'handler': _t_preview,
    },
    'lighthouse_get': {
        'description': 'Download a CID to a path on the box (Lighthouse gateway '
                       'first, public IPFS gateways as fallback). stdio only — an '
                       'HTTP caller shares no filesystem with the server and '
                       'should use lighthouse_preview or GET /get?cid= instead.',
        'inputSchema': {'type': 'object', 'properties': {
            'cid': _CID,
            'out': _str('where to write it (default ~/.mod/lighthouse/cache/<cid>)'),
        }, 'required': ['cid']},
        'annotations': _WRITE,
        'local': True,
        'handler': _t_get,
    },
    'lighthouse_list': {
        'description': "This module's own index — what you have pushed through it: "
                       'CID, name, size, when, and the gateway url. This is not the '
                       'store\'s list (lighthouse_objects) nor Lighthouse\'s '
                       '(lighthouse_account); it is the local record.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': _num('rows to return (default 100, max 1000)'),
            'scope': _str('whose rows', enum=['mine', 'all']),
        }},
        'annotations': _READ,
        'handler': _t_list,
    },
    'lighthouse_pin': {
        'description': 'Record a CID in this module\'s index. Uploads through '
                       'lighthouse_put are already pinned perpetually, so for our '
                       'own CIDs this is bookkeeping; for a foreign CID it is an '
                       'intent and the response says so.',
        'inputSchema': {'type': 'object', 'properties': {'cid': _CID},
                        'required': ['cid']},
        'annotations': _WRITE,
        'handler': _t_pin,
    },
    'lighthouse_forget': {
        'description': 'Drop a row from this module\'s index. It does NOT unpin: a '
                       'Lighthouse pin is perpetual and paid for, and the CID stays '
                       'retrievable by anyone who has it. If you need something to '
                       'stop being readable, that is a store visibility change, not '
                       'this.',
        'inputSchema': {'type': 'object', 'properties': {'cid': _CID},
                        'required': ['cid']},
        'annotations': {'readOnlyHint': False, 'destructiveHint': True,
                        'idempotentHint': True, 'openWorldHint': False},
        'handler': _t_forget,
    },
    'lighthouse_account': {
        'description': "Lighthouse's own view of the key this call is using: data "
                       'stored, data limit, and the files it lists under that key. '
                       'Use it to check headroom before a large upload, or to find '
                       'a CID uploaded outside this module.',
        'inputSchema': {'type': 'object', 'properties': {
            'uploads': _bool("include Lighthouse's file listing (default true)"),
            'page': _num('page of that listing (default 1)'),
            'key': _KEY,
        }},
        'annotations': _READ,
        'key_arg': 'key',
        'handler': _t_account,
    },
    'lighthouse_store': {
        'description': 'The store link from your side: reachable, your address, '
                       'whitelisted, terms accepted, quota, `blockers` and '
                       '`can_push`. This module holds no store credential — your '
                       'own signed token is forwarded, so this is genuinely what '
                       'the store thinks of YOU.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'annotations': _READ,
        'auth': False,
        'handler': _t_store,
    },
    'lighthouse_terms': {
        'description': "Read the store's terms, and sign them with accept=true. "
                       'The acceptance is proved by your own token, which is '
                       'forwarded verbatim — this server never signs for anybody. '
                       'Unsigned terms are the usual reason a push comes back 451.',
        'inputSchema': {'type': 'object', 'properties': {
            'accept': _bool('accept the terms as the signer of this call '
                            '(default false — just read them)')}},
        'annotations': _WRITE,
        'handler': _t_accept_terms,
    },
    'lighthouse_register': {
        'description': 'Reference a Lighthouse CID in the store — no bytes move. '
                       'This is the retry path when lighthouse_put reported '
                       '`store.registered: false`, and the way to bring a CID '
                       'uploaded elsewhere under the store\'s grants and market.',
        'inputSchema': {'type': 'object', 'properties': {
            'cid': _CID,
            'key': _str('name to file it under in the store'),
            'size': _num('size in bytes, when you know it'),
            'public': _PUBLIC,
            'pool': _POOL,
        }, 'required': ['cid']},
        'annotations': _WRITE,
        'handler': _t_register,
    },
    'lighthouse_objects': {
        'description': 'Your objects as the STORE holds them — visibility, pool, '
                       'backend, grants. Lighthouse-backed rows only unless '
                       'all_backends=true, which is how you find the localfs '
                       'objects worth making perpetual with lighthouse_mirror.',
        'inputSchema': {'type': 'object', 'properties': {
            'limit': _num('rows to return (default 200, max 1000)'),
            'all_backends': _bool('include objects on other store backends '
                                  '(default false)'),
        }},
        'annotations': _READ,
        'handler': _t_objects,
    },
    'lighthouse_mirror': {
        'description': 'Make something the store already holds perpetual: fetched '
                       'from the store with YOUR read rights, uploaded to '
                       'Lighthouse, registered back. `same_cid` reports whether the '
                       'content hashed identically — when it did not, chunking '
                       'differed and the Lighthouse CID is the registered one.',
        'inputSchema': {'type': 'object', 'properties': {
            'cid': _str('the CID the store already has'),
            'key': _str('name for the copy (defaults to the store object\'s name)'),
            'public': _PUBLIC,
            'pool': _POOL,
            'api_key': _KEY,
        }, 'required': ['cid']},
        'annotations': _WRITE,
        'key_arg': 'api_key',
        'handler': _t_mirror,
    },
    'lighthouse_set_key': {
        'description': 'Persist the deployment\'s Lighthouse API key off-chain in '
                       '~/.mod/lighthouse/credentials.json (0600) so later calls '
                       'need no key. stdio only, because it writes a shared '
                       'credential to this box — over HTTP the owner does it with '
                       'POST /key. To spend a key without storing it, pass `key` on '
                       'the call instead.',
        'inputSchema': {'type': 'object', 'properties': {
            'api_key': _str('the key from files.lighthouse.storage')},
            'required': ['api_key']},
        'annotations': _WRITE,
        'local': True,
        'handler': _t_set_key,
    },
}


# ── the spec surface ─────────────────────────────────────────────────

def version() -> str:
    return CONFIG.get('version') or '0.0.0'


def needs_auth(name) -> bool:
    """Does this tool act for a signer? Public reads say no."""
    tool = TOOLS.get(name)
    return bool(tool) and tool.get('auth', True)


def is_local_only(name) -> bool:
    return bool(TOOLS.get(name, {}).get('local'))


def tool_list():
    """tools/list — name, description, inputSchema, annotations."""
    return [{'name': name,
             'description': tool['description'],
             'inputSchema': tool['inputSchema'],
             'annotations': tool['annotations']}
            for name, tool in TOOLS.items()]


def client_config(url=None):
    """What to paste into an MCP client's config."""
    http = {'type': 'http', 'url': url or (CONFIG.get('urls', {}).get('mcp')
                                           or 'http://localhost:50680/mcp')}
    return {
        'http': {'mcpServers': {'lighthouse': http}},
        'stdio': {'mcpServers': {'lighthouse': {'command': 'python3',
                                                'args': [str(HERE / 'mcp.py')]}}},
        'claude_code': f'claude mcp add --transport http lighthouse {http["url"]}',
    }


def describe(url=None):
    """Everything about this server in one document — what GET /mcp serves and
    what the console renders, so the schema is never something you have to run
    a client to see."""
    return {
        'server': {'name': 'lighthouse', 'version': version(),
                   'description': CONFIG.get('description', '')[:400]},
        'protocol': {'default': DEFAULT_PROTOCOL_VERSION,
                     'supported': list(SUPPORTED_PROTOCOL_VERSIONS),
                     'jsonrpc': '2.0',
                     'methods': ['initialize', 'ping', 'tools/list', 'tools/call']},
        'transports': {
            'http': {'endpoint': 'POST /mcp', 'schema': 'GET /mcp',
                     'url': url or (CONFIG.get('urls', {}).get('mcp')
                                    or 'http://localhost:50680/mcp'),
                     'note': 'Streamable HTTP. Filesystem tools are refused here.'},
            'stdio': {'command': f'python3 {HERE / "mcp.py"}',
                      'note': 'runs with this box\'s own keys — every tool available'},
        },
        'auth': {
            'protocol_token': 'Authorization: Bearer <mod-protocol token> — who you '
                              'are to this module and to the store, forwarded '
                              'verbatim. Required for every tool except '
                              + ', '.join(n for n in TOOLS if not needs_auth(n)),
            'lighthouse_key': 'the `key` argument (or `api_key` on the upload '
                              'tools) — spends YOUR Lighthouse account for this '
                              'call and is never written to disk. Omit it to use '
                              "the deployment's key.",
            'stdio': 'a stdio server mints the token with the box\'s own mod key, '
                     'so local tools need no argument',
        },
        'instructions': INSTRUCTIONS,
        'count': len(TOOLS),
        'tools': [{'name': name,
                   'description': tool['description'],
                   'inputSchema': tool['inputSchema'],
                   'annotations': tool['annotations'],
                   'auth': 'token' if tool.get('auth', True) else 'none',
                   'transports': ['stdio'] if tool.get('local') else ['stdio', 'http']}
                  for name, tool in TOOLS.items()],
        'config': client_config(url),
    }


# ── JSON-RPC 2.0 ─────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args, ctx=None):
    """Run one tool. Raises Refused/ValueError with a message worth reading.

    A Lighthouse key passed as an argument is lifted out of the arguments and
    into the caller's context here, once, rather than in each handler — and
    because it lives on the context it is never echoed back in a result. Which
    argument carries it differs by tool: on the upload tools `key` is already
    the object's *name*, so the credential is `api_key` there.
    """
    tool = TOOLS.get(name)
    if not tool:
        raise Refused(f'unknown tool: {name} — have {", ".join(TOOLS)}')
    args = dict(args or {})
    ctx = ctx or LOCAL_CTX
    key_arg = tool.get('key_arg')
    if key_arg:
        ctx = ctx.with_key(args.pop(key_arg, None))
    return tool['handler'](args, ctx)


def _call(id_, params, ctx):
    name = str(params.get('name') or '')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _error(id_, -32602, 'arguments must be an object')

    def failed(text):
        # A tool failure is a *successful* JSON-RPC response carrying isError,
        # per the MCP spec, so the model reads the reason and retries instead
        # of the transport dying under it.
        return _result(id_, {'content': [{'type': 'text', 'text': text}],
                             'isError': True})
    try:
        result = call_tool(name, args, ctx)
    except Refused as e:
        return failed(f'{name}: {e}')
    except StoreError as e:
        return failed(f'{name}: store {e.status} — {e.message}')
    except KeyError as e:
        return failed(f'{name}: missing argument {e}')
    except TypeError as e:
        return failed(f'{name}: bad arguments — {e}')
    except FileNotFoundError as e:
        return failed(f'{name}: no such file — {e}')
    except Exception as e:
        return failed(f'{name} failed: {type(e).__name__}: {e}')
    text = result if isinstance(result, str) else json.dumps(result, indent=2,
                                                             default=str)
    out = {'content': [{'type': 'text', 'text': text}], 'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _result(id_, out)


def handle(body, ctx=None):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        asked = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': (asked if asked in SUPPORTED_PROTOCOL_VERSIONS
                                else DEFAULT_PROTOCOL_VERSION),
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': 'lighthouse', 'version': version()},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params, ctx)
    return _error(id_, -32601, f'method not found: {method}')


# ── transports ───────────────────────────────────────────────────────

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
            response = handle(body, LOCAL_CTX)
        if response is not None:
            sys.stdout.write(json.dumps(response, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--tools' in argv or '--schema' in argv:
        print(json.dumps(describe(), indent=2))
    elif '--http' in argv:
        # The HTTP transport lives in the API server, so there is one mounting
        # of these tools and not two that can disagree.
        import uvicorn

        from api.api import app
        i = argv.index('--port') + 1 if '--port' in argv else -1
        port = int(argv[i]) if i > 0 else int(os.environ.get(
            'LIGHTHOUSE_API_PORT', CONFIG.get('port', 50680)))
        uvicorn.run(app, host='0.0.0.0', port=port)
    else:
        serve_stdio()
