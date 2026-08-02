"""
mcp — the hub, as an MCP server.

A directory of MCP servers that a model can't query over MCP would be a joke,
so the hub speaks its own protocol: POST /mcp is JSON-RPC 2.0 over Streamable
HTTP, and an agent can search every registry, read a server card, probe a live
endpoint for its real tools, get a paste-ready client config, and publish its
own server — all without a bespoke SDK.

Hand-rolled JSON-RPC (no `mcp` package) and every tool wraps an endpoint
function from api.api, so auth and validation behave exactly like the REST
surface. Responses are plain JSON: one request per POST, one response back.

Auth: the same `Authorization: Bearer <mod protocol token>` header. Public
tools work anonymously; the publishing tools turn a 401/403 into an isError
*tool result* so the calling model reads a usable message instead of hitting a
dead transport.
"""
import inspect
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

# api.api imports this router at the bottom of its module body, so every name
# we touch exists by the time a handler runs. Attribute access stays lazy so
# test reloads and monkeypatches of api.api are honored here too.
from api import api as hub_api

router = APIRouter()

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'A hub of MCP servers. mcp_search fans out across the official MCP '
    'registry, GitHub, npm, Glama, Smithery, curated awesome-lists, the local '
    'mod fleet and servers published here, then merges duplicates into one '
    'ranked list — open-source servers only unless you pass oss=false. '
    'mcp_probe speaks MCP to a live remote endpoint and returns its real tool '
    'inventory. mcp_client_config emits paste-ready config for Claude/Cursor/'
    'VS Code. Publishing (mcp_submit) needs an Authorization: Bearer <mod '
    'protocol token> header; the manifest is pinned by CID to the store mod '
    'under the publisher\'s own address, which requires sign-accepting store\'s '
    'terms first (mcp_terms).'
)


def _req(args: dict, key: str) -> str:
    v = str(args.get(key) or '').strip()
    if not v:
        raise ValueError(f'{key} required')
    return v


# ── tool handlers (thin wrappers over api.api endpoint functions) ──

def _t_sources(args, auth):
    return hub_api.sources()


def _t_search(args, auth):
    return hub_api.search(
        q=str(args.get('q') or ''), sources=str(args.get('sources') or ''),
        limit=int(args.get('limit') or 20),
        oss=bool(args.get('oss', True)),
        transport=str(args.get('transport') or ''),
        license=str(args.get('license') or ''), tag=str(args.get('tag') or ''),
        category=str(args.get('category') or ''),
        sort=str(args.get('sort') or 'relevance'))


def _t_server(args, auth):
    return hub_api.server(id=_req(args, 'id'))


def _t_probe(args, auth):
    return hub_api.probe(hub_api.ProbeBody(
        url=args.get('url'), id=args.get('id'), token=args.get('token'),
        refresh=bool(args.get('refresh'))))


def _t_client_config(args, auth):
    return hub_api.client_config(id=_req(args, 'id'),
                                 client=str(args.get('client') or 'claude'))


def _t_stats(args, auth):
    return hub_api.stats()


def _t_terms(args, auth):
    if args.get('accept'):
        return hub_api.store_terms_accept(authorization=auth)
    return hub_api.store_terms(authorization=auth)


def _t_submit(args, auth):
    body = hub_api.SubmitBody(
        name=_req(args, 'name'), description=_req(args, 'description'),
        slug=args.get('slug'), title=args.get('title'), repo=args.get('repo'),
        homepage=args.get('homepage'), license=args.get('license'),
        version=args.get('version'), tags=list(args.get('tags') or []),
        transports=list(args.get('transports') or []),
        remote_url=args.get('remote_url'), npm=args.get('npm'),
        pypi=args.get('pypi'))
    return hub_api.submit(body, authorization=auth)


def _t_submissions(args, auth):
    return hub_api.submissions(mine=bool(args.get('mine')), authorization=auth)


TOOLS = {
    'mcp_sources': {
        'description': 'The directories this hub aggregates (official MCP '
                       'registry, GitHub, npm, Glama, Smithery, awesome-lists, '
                       'the local mod fleet, hub submissions) with what each '
                       'indexes and its cache TTL. Public.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_sources,
    },
    'mcp_search': {
        'description': 'Search every MCP directory at once and get one ranked '
                       'list — the same project listed in four places collapses '
                       'into a single card carrying stars, downloads, license, '
                       'transports and install recipes. Open-source servers '
                       'only unless oss=false. Public.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': {'type': 'string', 'description': 'what the server should do, e.g. "postgres" or "browser automation"'},
            'sources': {'type': 'string', 'description': 'comma-separated provider ids to limit the scan (see mcp_sources)'},
            'oss': {'type': 'boolean', 'description': 'true (default) = only servers with public source'},
            'transport': {'type': 'string', 'enum': ['stdio', 'streamable-http', 'sse'], 'description': 'only servers speaking this transport'},
            'license': {'type': 'string', 'description': 'exact SPDX license filter, e.g. MIT'},
            'tag': {'type': 'string', 'description': 'exact tag filter'},
            'category': {'type': 'string', 'description': 'coarse category: dev/data/web/cloud/files/comms/ai/finance/security'},
            'sort': {'type': 'string', 'enum': ['relevance', 'stars', 'downloads', 'new', 'name']},
            'limit': {'type': 'integer', 'description': 'max servers (default 20)'},
        }},
        'handler': _t_search,
    },
    'mcp_server': {
        'description': 'One server card by id, merged across every directory '
                       'that lists it, with install recipes and the last probe '
                       'result when the hub has one. Public.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': 'server id from mcp_search, e.g. npm:@foo/bar or official:io.github.foo/bar'},
        }, 'required': ['id']},
        'handler': _t_server,
    },
    'mcp_probe': {
        'description': 'Speak MCP to a live remote server (initialize + '
                       'tools/list) and return its protocol version, server '
                       'info and REAL tool inventory — what the directories '
                       'claim is often stale. Give a url, or an id whose card '
                       'advertises a remote endpoint. stdio-only servers cannot '
                       'be probed. Public.',
        'inputSchema': {'type': 'object', 'properties': {
            'url': {'type': 'string', 'description': 'remote MCP endpoint URL'},
            'id': {'type': 'string', 'description': 'server id to resolve a URL from'},
            'token': {'type': 'string', 'description': 'bearer token for the probed server, if it needs one'},
            'refresh': {'type': 'boolean', 'description': 'bypass the 15-minute probe cache'},
        }},
        'handler': _t_probe,
    },
    'mcp_client_config': {
        'description': 'Paste-ready MCP client configuration for a server, plus '
                       'the equivalent `claude mcp add` command and the file it '
                       'belongs in. Public.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': {'type': 'string', 'description': 'server id'},
            'client': {'type': 'string', 'enum': ['claude', 'cursor', 'vscode'], 'description': 'target client (default claude)'},
        }, 'required': ['id']},
        'handler': _t_client_config,
    },
    'mcp_stats': {
        'description': 'Hub totals: providers wired, fleet servers reachable '
                       'here, submissions, how many manifests are pinned, cache '
                       'state. Public.',
        'inputSchema': {'type': 'object', 'properties': {}},
        'handler': _t_stats,
    },
    'mcp_terms': {
        'description': "store's publisher terms of service, which must be "
                       'sign-accepted before a manifest can be pinned. Call '
                       'with accept=true (authed) to record acceptance.',
        'inputSchema': {'type': 'object', 'properties': {
            'accept': {'type': 'boolean', 'description': 'true = sign-accept the current version (requires auth)'},
        }},
        'handler': _t_terms,
    },
    'mcp_submit': {
        'description': 'Publish an MCP server to the hub. The manifest is '
                       "pinned to the store mod under the caller's own address "
                       'and the CID is recorded here. Needs a name, a '
                       'description, and at least one of repo / remote_url / '
                       'npm / pypi. Requires auth + accepted terms.',
        'inputSchema': {'type': 'object', 'properties': {
            'name': {'type': 'string', 'description': 'server name'},
            'description': {'type': 'string', 'description': 'what it does'},
            'repo': {'type': 'string', 'description': 'public source repository URL'},
            'homepage': {'type': 'string'},
            'license': {'type': 'string', 'description': 'SPDX id, e.g. MIT'},
            'version': {'type': 'string'},
            'remote_url': {'type': 'string', 'description': 'hosted Streamable HTTP endpoint, if any'},
            'npm': {'type': 'string', 'description': 'npm package name for npx install'},
            'pypi': {'type': 'string', 'description': 'PyPI package name for uvx install'},
            'tags': {'type': 'array', 'items': {'type': 'string'}},
            'transports': {'type': 'array', 'items': {'type': 'string', 'enum': ['stdio', 'streamable-http', 'sse']}},
            'slug': {'type': 'string', 'description': 'hub id override (defaults to the slugified name)'},
        }, 'required': ['name', 'description']},
        'handler': _t_submit,
    },
    'mcp_submissions': {
        'description': 'Servers published to this hub, newest first, with their '
                       'manifest CIDs. mine=true (authed) narrows to yours.',
        'inputSchema': {'type': 'object', 'properties': {
            'mine': {'type': 'boolean', 'description': 'only your own submissions (requires auth)'},
        }},
        'handler': _t_submissions,
    },
}


def _rpc_result(id_, result) -> JSONResponse:
    return JSONResponse({'jsonrpc': '2.0', 'id': id_, 'result': result})


def _rpc_error(id_, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse({'jsonrpc': '2.0', 'id': id_,
                         'error': {'code': code, 'message': message}},
                        status_code=status)


def _tool_error(id_, message: str) -> JSONResponse:
    # Tool failures are *successful* JSON-RPC responses carrying isError — per
    # the MCP spec — so the calling model can read the message and correct.
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
            detail += (' — this tool needs a mod protocol token: send it as an '
                       'Authorization: Bearer <token> header on POST /mcp')
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
        return _rpc_error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 '
                          'object with a method', status=400)
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
            'serverInfo': {'name': 'mcp-hub',
                           'version': str(hub_api.CONFIG.get('version') or '1.0.0')},
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
