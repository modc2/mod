"""
store mcp — Model Context Protocol endpoint (Streamable HTTP transport).

POST /mcp speaks JSON-RPC 2.0 so any MCP client (Claude, IDEs, agent
frameworks) can drive the store as a set of tools — browse the market,
search/read objects, upload text, share, pin — without a bespoke SDK.

Self-contained by design: the JSON-RPC handling is hand-rolled (no `mcp`
package) and every tool wraps an existing endpoint function from api.api,
so auth, quotas, the terms gate and read-gating behave exactly like the
REST surface. Responses are plain JSON (no SSE) — each POST carries one
request and gets one response, which is all the tool workflow needs.

Auth: the same `Authorization: Bearer <mod protocol token>` header the REST
API uses. Public tools work anonymously; authed tools convert the 401/403
HTTPException into an isError *tool result* (not an HTTP error) so MCP
clients surface a usable message instead of a dead transport.
"""
import inspect
import io
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse

# api.api imports this router at the bottom of its module body, so by the
# time any handler runs every name we touch on `store_api` exists. Attribute
# access stays lazy (module-level, not from-imports) so test reloads and
# monkeypatches of api.api are honored here too.
from api import api as store_api

router = APIRouter()

# Echo the client's protocol version when we know it; otherwise pin the
# oldest revision whose feature set (plain-JSON Streamable HTTP, tools)
# this server fully implements.
SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Decentralized CID-addressed storage (localfs/filecoin/hippius/'
    'lighthouse/external refs) with private-by-default objects, timed '
    'access grants, pools and a BlocTime-priced marketplace. Public tools '
    '(store_status, store_market_browse, store_terms, store_get on public '
    'objects) need no auth; everything else wants an Authorization: Bearer '
    '<mod protocol token> header. Storing text additionally requires the '
    'caller to be authorized (whitelist/owner/BlocTime) and to have '
    'sign-accepted the current terms of service.'
)


def _req(args: dict, key: str) -> str:
    v = str(args.get(key) or '').strip()
    if not v:
        raise ValueError(f'{key} required')
    return v


# ── tool handlers (thin wrappers over api.api endpoint functions) ──

def _t_status(args, auth):
    return store_api.status()


def _t_market_browse(args, auth):
    return store_api.market_browse(
        q=str(args.get('q') or ''), tag=str(args.get('tag') or ''),
        seller=str(args.get('seller') or ''), sort=str(args.get('sort') or 'hot'),
        free='1' if args.get('free') else None,
        limit=int(args.get('limit') or 100), authorization=auth)


def _t_terms(args, auth):
    return store_api.terms_get(authorization=auth)


def _t_me(args, auth):
    return store_api.me(authorization=auth)


def _t_list(args, auth):
    return store_api.list_objects(backend=args.get('backend'),
                                  limit=int(args.get('limit') or 100),
                                  authorization=auth)


def _t_search(args, auth):
    return store_api.search(
        q=str(args.get('q') or ''), backend=args.get('backend'),
        scheme=args.get('scheme'), visibility=args.get('visibility'),
        semantic_q=args.get('semantic_q'), scope=str(args.get('scope') or 'mine'),
        limit=int(args.get('limit') or 200), authorization=auth)


def _t_get(args, auth):
    return store_api.preview(cid=_req(args, 'cid'),
                             max_bytes=int(args.get('max_bytes') or 65536),
                             token=None, authorization=auth)


def _t_object_info(args, auth):
    return store_api.object_info(cid=_req(args, 'cid'), authorization=auth)


async def _t_put_text(args, auth):
    # Reuse the real /put path (terms gate, quota, semantic hash, ACL,
    # CID-ref graph) by feeding the text through an in-memory UploadFile.
    text = args.get('text')
    if text is None:
        raise ValueError('text required')
    if not isinstance(text, str):
        text = json.dumps(text)
    upload = UploadFile(file=io.BytesIO(text.encode('utf-8')),
                        filename=_req(args, 'name'))
    return await store_api.put(
        file=upload, backend=str(args.get('backend') or 'localfs'), key=None,
        public='true' if args.get('public') else 'false',
        pool=args.get('pool'), authorization=auth)


def _t_share(args, auth):
    ttl = int(args.get('ttl_seconds') or 3600)
    return store_api.grant_create(
        store_api.GrantBody(grantee=_req(args, 'grantee'), cid=_req(args, 'cid'),
                            scope='read', ttl_seconds=ttl),
        authorization=auth)


def _t_pin(args, auth):
    return store_api.pin(
        store_api.PinBody(cid=_req(args, 'cid'),
                          backend=str(args.get('backend') or 'filecoin')),
        authorization=auth)


def _t_pins(args, auth):
    return store_api.pins_list(authorization=auth)


def _t_pools(args, auth):
    return store_api.pool_list(authorization=auth)


TOOLS = {
    'store_status': {
        'description': 'Store service status: configured backends and index '
                       'health. Public, no auth needed.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_status,
    },
    'store_market_browse': {
        'description': 'Browse the public marketplace of listed objects. '
                       'Filter by text query, tag or seller; sort hot/new/top; '
                       'free=true shows free drops only. Public, no auth needed.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': {'type': 'string', 'description': 'full-text query over title/description/tags'},
            'tag': {'type': 'string', 'description': 'exact tag filter'},
            'seller': {'type': 'string', 'description': '0x seller address filter'},
            'sort': {'type': 'string', 'enum': ['hot', 'new', 'top'], 'description': 'ranking (default hot)'},
            'free': {'type': 'boolean', 'description': 'true = free listings only'},
            'limit': {'type': 'integer', 'description': 'max listings (default 100)'},
        }},
        'handler': _t_market_browse,
    },
    'store_terms': {
        'description': 'Current terms of service text and version. Uploaders '
                       'must sign-accept these before storing. Public.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_terms,
    },
    'store_me': {
        'description': "Caller identity: address, authorized/admin flags, "
                       "quota usage and terms-acceptance state. Requires auth.",
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_me,
    },
    'store_list': {
        'description': "List the caller's stored objects with visibility, "
                       'scheme and semantic hash. Requires auth.',
        'inputSchema': {'type': 'object', 'properties': {
            'backend': {'type': 'string', 'description': 'filter by backend (localfs/filecoin/hippius/lighthouse/external)'},
            'limit': {'type': 'integer', 'description': 'max objects (default 100)'},
        }},
        'handler': _t_list,
    },
    'store_search': {
        'description': "Search the caller's objects (and those shared with "
                       'them via scope). q matches CID/key substring; '
                       'semantic_q ranks by 1-bit semantic similarity, '
                       'nearest first. Requires auth.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': {'type': 'string', 'description': 'CID/key substring filter'},
            'semantic_q': {'type': 'string', 'description': 'rank results by semantic similarity to this text'},
            'scope': {'type': 'string', 'enum': ['mine', 'shared', 'all'], 'description': 'which objects to search (default mine)'},
            'backend': {'type': 'string'},
            'scheme': {'type': 'string'},
            'visibility': {'type': 'string', 'enum': ['public', 'private']},
            'limit': {'type': 'integer', 'description': 'max results (default 200)'},
        }},
        'handler': _t_search,
    },
    'store_get': {
        'description': "Preview an object's content by CID: up to max_bytes "
                       '(text decoded when possible) plus full size and a '
                       'truncated flag. Public objects work without auth; '
                       'private ones need an authorized caller.',
        'inputSchema': {'type': 'object', 'properties': {
            'cid': {'type': 'string', 'description': 'object CID'},
            'max_bytes': {'type': 'integer', 'description': 'preview byte cap (default 65536, max 1 MiB)'},
        }, 'required': ['cid']},
        'handler': _t_get,
    },
    'store_object_info': {
        'description': 'Full object profile: owner, stored-at, backends, '
                       'visibility, semantic hash, pinned state and the '
                       'CID-reference links graph (in/out). Owners also see '
                       'the access roster (grants + pools).',
        'inputSchema': {'type': 'object', 'properties': {
            'cid': {'type': 'string', 'description': 'object CID'},
        }, 'required': ['cid']},
        'handler': _t_object_info,
    },
    'store_put_text': {
        'description': 'Store a text or JSON payload as a content-addressed '
                       'object. Private by default (public=true to open); '
                       'optionally drop it straight into a pool. Requires an '
                       'authorized caller who has sign-accepted the terms.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'description': 'filename/key for the object'},
            'text': {'type': 'string', 'description': 'the content to store'},
            'backend': {'type': 'string', 'description': 'storage backend (default localfs)'},
            'public': {'type': 'boolean', 'description': 'true = world-readable (default false)'},
            'pool': {'type': 'string', 'description': 'pool id to add the object to'},
        }, 'required': ['name', 'text']},
        'handler': _t_put_text,
    },
    'store_share': {
        'description': 'Grant another address timed read access to an object '
                       'you own. The grant auto-expires after ttl_seconds. '
                       'Requires auth.',
        'inputSchema': {'type': 'object', 'properties': {
            'grantee': {'type': 'string', 'description': '0x address to share with'},
            'cid': {'type': 'string', 'description': 'object CID to share'},
            'ttl_seconds': {'type': 'integer', 'description': 'grant lifetime in seconds (default 3600)'},
        }, 'required': ['grantee', 'cid']},
        'handler': _t_share,
    },
    'store_pin': {
        'description': 'Pin a CID on a backend so it stays retrievable. '
                       'Requires an authorized caller.',
        'inputSchema': {'type': 'object', 'properties': {
            'cid': {'type': 'string', 'description': 'CID to pin'},
            'backend': {'type': 'string', 'description': 'backend to pin on (default filecoin)'},
        }, 'required': ['cid']},
        'handler': _t_pin,
    },
    'store_pins': {
        'description': "List the caller's pinned objects with size/key/"
                       'visibility. Requires auth.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_pins,
    },
    'store_pools': {
        'description': 'List the data pools the caller owns or belongs to '
                       '(mutual-access shared spaces). Requires auth.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_pools,
    },
}


def _rpc_result(id_, result) -> JSONResponse:
    return JSONResponse({'jsonrpc': '2.0', 'id': id_, 'result': result})


def _rpc_error(id_, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse({'jsonrpc': '2.0', 'id': id_,
                         'error': {'code': code, 'message': message}},
                        status_code=status)


def _tool_error(id_, message: str) -> JSONResponse:
    # Tool failures (bad args, missing auth, 4xx from the wrapped endpoint)
    # are *successful* JSON-RPC responses carrying isError — per MCP spec —
    # so the client model can read the message and correct course.
    return _rpc_result(id_, {'content': [{'type': 'text', 'text': message}],
                             'isError': True})


async def _call_tool(id_, params: dict, authorization: Optional[str]) -> JSONResponse:
    name = str(params.get('name') or '')
    tool = TOOLS.get(name)
    if not tool:
        return _rpc_error(id_, -32602, f'unknown tool: {name}')
    args = params.get('arguments') or {}
    if not isinstance(args, dict):
        return _rpc_error(id_, -32602, 'arguments must be an object')
    try:
        result = tool['handler'](args, authorization)
        if inspect.isawaitable(result):
            result = await result
    except HTTPException as e:
        detail = e.detail if isinstance(e.detail, str) else json.dumps(e.detail)
        if e.status_code == 401:
            detail += (' — this tool needs a mod protocol token: send it as '
                       'an Authorization: Bearer <token> header on POST /mcp')
        return _tool_error(id_, f'{name} failed ({e.status_code}): {detail}')
    except Exception as e:
        return _tool_error(id_, f'{name} failed: {type(e).__name__}: {e}')
    out = {'content': [{'type': 'text',
                        'text': json.dumps(result, indent=2, default=str)}],
           'isError': False}
    if isinstance(result, dict):
        out['structuredContent'] = result
    return _rpc_result(id_, out)


@router.get('/mcp')
def mcp_get():
    """Streamable HTTP without SSE: nothing to GET — clients must POST."""
    return Response(status_code=405, media_type='text/plain',
                    content='POST JSON-RPC 2.0 messages to this endpoint')


@router.post('/mcp')
async def mcp_post(request: Request):
    """MCP Streamable HTTP endpoint: one JSON-RPC message per POST."""
    authorization = request.headers.get('authorization')
    try:
        body = json.loads((await request.body()) or b'')
    except Exception:
        return _rpc_error(None, -32700, 'parse error: body is not valid JSON',
                          status=400)
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _rpc_error(id_, -32600,
                          'invalid request: expected a JSON-RPC 2.0 object '
                          'with a method', status=400)
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    # Notifications (no id, or notifications/*) get an empty 202 per spec.
    if id_ is None or method.startswith('notifications/'):
        return Response(status_code=202)
    if method == 'initialize':
        client_ver = str(params.get('protocolVersion') or '')
        return _rpc_result(id_, {
            'protocolVersion': client_ver if client_ver in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'store',
                           'version': str(store_api.CONFIG.get('version') or '1.0.0')},
            'instructions': INSTRUCTIONS,
        })
    if method == 'ping':
        return _rpc_result(id_, {})
    if method == 'tools/list':
        return _rpc_result(id_, {'tools': [
            {'name': n, 'description': t['description'],
             'inputSchema': t['inputSchema']} for n, t in TOOLS.items()]})
    if method == 'tools/call':
        return await _call_tool(id_, params, authorization)
    return _rpc_error(id_, -32601, f'method not found: {method}')
