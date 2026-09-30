"""
bt.server — HTTP surface for the bt module on one port (:50280).

  GET  /            the console — a static Next.js export (app/dist, see
                    app/build.sh); every page route maps to dist/<page>.html
  GET  /legacy      the single-file console it replaced (app/legacy.html),
                    also served at / whenever no build has been published
  GET  /api         module info (mod protocol null-call convention)
  GET  /api/tools   MCP-shaped tool listing
  GET  /api/docs    grouped tool docs for the Docs section
  POST /api/call    {"tool": name, "args": {...}} -> {"ok", "result"|"error"}
  GET  /api/agent/card    the agent card (also at /.well-known/agent.json)
  GET  /api/agent/status  auth, model, tool count, runs in flight
  GET  /api/agent/tools   the agent's toolbox, grouped
  GET  /api/agent/chats   conversations · /api/agent/chats/{id} one, with messages
  POST /api/agent/chat    {"message", "chat", "context"} -> SSE run
  POST /api/agent/ask     the same turn, run to completion, one JSON reply
  POST /api/agent/stop    {"chat"} -> kill the run in flight
  POST /mcp         MCP streamable-HTTP endpoint (same JSON-RPC as stdio)

Run:  python3 -m bt.server   (or pm2: bt-app)
"""
from __future__ import annotations

import json
import os
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import agent, chats, history, tools, traders
from .mcp_server import PROTOCOL_VERSION, SERVER_INFO

PORT = int(os.environ.get('BT_PORT', '50280'))
APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app')

app = FastAPI(title='bt', docs_url=None, redoc_url=None,
              on_startup=[history.start, traders.start])
app.add_middleware(CORSMiddleware, allow_origins=['*'],
                   allow_methods=['*'], allow_headers=['*'])


# API paths as they arrive once the gateway has stripped `/bt/api`
# (modc2.com/bt/api/call reaches us as /call — the mod protocol's canonical
# form). /docs and / are also console pages, so those two only count as API
# when the caller is not a browser asking for HTML.
_API_ROOT = {'/call', '/tools', '/ask', '/agent'}
_API_SHARED = {'/', '/docs'}


def _api_path(path: str, request: Request) -> str | None:
    if path.startswith('/_api/') or path == '/_api':
        return ('/api' + path[5:]).rstrip('/')   # the console's own unstripped route
    if path in _API_ROOT or path.startswith('/agent/') or path.startswith('/.well-known/'):
        return '/api' + path if not path.startswith('/.well-known/') else path
    if path in _API_SHARED and 'text/html' not in request.headers.get('accept', ''):
        return '/api' + (path if path != '/' else '')
    return None


@app.middleware('http')
async def strip_gateway_prefix(request: Request, call_next):
    # The gateway proxies modc2.com/bt with the /bt prefix kept, and
    # modc2.com/bt/api/* with /bt/api stripped. Both land on the same routes.
    path = request.scope['path']
    if path == '/bt' or path.startswith('/bt/'):
        path = path[3:] or '/'
    request.scope['path'] = _api_path(path, request) or path
    return await call_next(request)


def _info():
    return {
        'name': 'bt',
        'description': 'Bittensor protocol console + MCP server',
        'version': SERVER_INFO['version'],
        'app': 'next' if os.path.isdir(DIST_DIR) else 'legacy',
        'network': tools.DEFAULT_NETWORK,
        'tools': len(tools.TOOLS),
        'block': history.stats().get('block'),   # what the index is synced to
        'traders': traders.stats(),
        'agent': {'ready': agent.status()['ready'], 'model': agent.MODEL,
                  'card': '/api/agent/card', 'chat': '/api/agent/chat'},
        'chats': chats.stats(),
        'mcp': {'http': '/bt/mcp', 'stdio': 'python3 -m bt.mcp_server'},
        'time': int(time.time()),
    }


@app.get('/api')
@app.get('/_api')
def api_info():
    return _info()


@app.get('/api/tools')
def api_tools():
    return {'tools': tools.list_tools()}


@app.get('/api/docs')
def api_docs():
    return {'groups': tools.docs()}


@app.post('/api/call')
def api_call(body: dict):
    name = body.get('tool')
    args = body.get('args') or {}
    t0 = time.time()
    try:
        result = tools.call_tool(name, args)
        return {'ok': True, 'tool': name, 'ms': int((time.time() - t0) * 1000),
                'result': result}
    except Exception as e:
        return JSONResponse(status_code=400, content={
            'ok': False, 'tool': name, 'error': f'{type(e).__name__}: {e}'})


@app.get('/api/agent')
@app.get('/api/agent/status')
def api_agent():
    return agent.status()


@app.get('/api/agent/card')
@app.get('/.well-known/agent.json')
@app.get('/api/.well-known/agent.json')
def api_agent_card():
    """Who the agent is and how to talk to it — the discovery document."""
    return agent.card()


@app.get('/api/agent/tools')
def api_agent_tools():
    """The toolbox the chat plays, grouped — the console's help, too."""
    return {'groups': tools.docs(), 'count': len(tools.TOOLS),
            'allowed': len(agent.ALLOWED_TOOLS),
            'denied': [n.replace('mcp__bittensor__', '')
                       for n in agent.DISALLOWED_TOOLS]}


@app.get('/api/agent/chats')
def api_chats(limit: int = 50):
    return {'chats': chats.list_chats(limit=limit), 'stats': chats.stats()}


@app.get('/api/agent/chats/{chat_id}')
def api_chat(chat_id: str):
    got = chats.get(chat_id)
    if got is None:
        return JSONResponse(status_code=404, content={
            'ok': False, 'error': f'no such chat: {chat_id}'})
    return got


@app.post('/api/agent/chats/{chat_id}/rename')
def api_chat_rename(chat_id: str, body: dict):
    return chats.rename(chat_id, str(body.get('title') or ''))


@app.delete('/api/agent/chats/{chat_id}')
def api_chat_delete(chat_id: str):
    return chats.delete(chat_id)


def _sse(events):
    def gen():
        for ev in events:
            yield 'data: ' + json.dumps(ev, default=str) + '\n\n'
    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'cache-control': 'no-cache',
                                      'x-accel-buffering': 'no'})


@app.post('/api/agent/chat')
@app.post('/api/ask')          # the pre-2.4 name, same stream
def api_agent_chat(body: dict):
    message = (body.get('message') or body.get('question') or '').strip()
    if not message:
        return JSONResponse(status_code=400, content={
            'ok': False, 'error': 'message required'})
    return _sse(agent.chat(message, chat_id=body.get('chat'),
                           context=body.get('context')))


@app.post('/api/agent/ask')
def api_agent_ask(body: dict):
    """One turn, run to completion — for callers that cannot read a stream."""
    message = (body.get('message') or body.get('question') or '').strip()
    if not message:
        return JSONResponse(status_code=400, content={
            'ok': False, 'error': 'message required'})
    return agent.ask(message, chat_id=body.get('chat'),
                     context=body.get('context'))


@app.post('/api/agent/stop')
def api_agent_stop(body: dict):
    return agent.stop(str(body.get('chat') or ''))


# ------------------------------------------------------------ MCP over HTTP

def _mcp_handle(msg: dict):
    method = msg.get('method')
    id_ = msg.get('id')
    if id_ is None:  # notification
        return None
    if method == 'initialize':
        client_ver = (msg.get('params') or {}).get('protocolVersion')
        result = {'protocolVersion': client_ver or PROTOCOL_VERSION,
                  'capabilities': {'tools': {}}, 'serverInfo': SERVER_INFO}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': tools.list_tools()}
    elif method == 'tools/call':
        params = msg.get('params') or {}
        try:
            out = tools.call_tool(params.get('name'), params.get('arguments') or {})
            result = {'content': [{'type': 'text',
                                   'text': json.dumps(out, indent=2, default=str)}],
                      'isError': False}
        except Exception as e:
            result = {'content': [{'type': 'text',
                                   'text': f'{type(e).__name__}: {e}'}],
                      'isError': True}
    else:
        return {'jsonrpc': '2.0', 'id': id_,
                'error': {'code': -32601, 'message': f'method not found: {method}'}}
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


@app.post('/mcp')
async def mcp_endpoint(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            'jsonrpc': '2.0', 'id': None,
            'error': {'code': -32700, 'message': 'parse error'}})
    msgs = body if isinstance(body, list) else [body]
    replies = [r for r in (_mcp_handle(m) for m in msgs) if r is not None]
    if not replies:
        return JSONResponse(status_code=202, content=None)
    return replies[0] if not isinstance(body, list) else replies


@app.get('/mcp')
def mcp_get(request: Request):
    # /mcp is also the console's "connect via MCP" page — browsers get that
    if 'text/html' in request.headers.get('accept', ''):
        return console('mcp')
    return JSONResponse(status_code=405, content={
        'error': 'POST JSON-RPC here (MCP streamable HTTP); SSE stream not offered'})


# ------------------------------------------------------------------- app
#
# The console is app/ — a Next.js app exported to static files (build.sh
# publishes app/dist -> app/releases/<t>). No node process at runtime: this
# server hands the files out next to /api and /mcp. Routes resolve the way
# `next export` lays them out: /markets -> markets.html, /_next/... as-is.
# Registered LAST, so it can never shadow an API route.

DIST_DIR = os.path.join(APP_DIR, 'dist')
LEGACY = os.path.join(APP_DIR, 'legacy.html')
_IMMUTABLE = {'cache-control': 'public, max-age=31536000, immutable'}
_FRESH = {'cache-control': 'no-cache'}


def _dist_file(path: str):
    """The file under app/dist a request path maps to, or None."""
    root = os.path.realpath(DIST_DIR)
    if not os.path.isdir(root):
        return None
    rel = path.strip('/') or 'index'
    for cand in (rel, rel + '.html', os.path.join(rel, 'index.html')):
        full = os.path.realpath(os.path.join(root, cand))
        if (full == root or full.startswith(root + os.sep)) and os.path.isfile(full):
            return full
    return None


@app.get('/legacy')
def legacy():
    return FileResponse(LEGACY, headers=_FRESH)


@app.get('/')
@app.get('/{path:path}')
def console(path: str = ''):
    f = _dist_file(path)
    if f:
        return FileResponse(f, headers=_IMMUTABLE if '/_next/static/' in f else _FRESH)
    if not os.path.isdir(DIST_DIR):
        # never built on this host — the single-file console still works
        if path.strip('/') in ('', 'index.html'):
            return FileResponse(LEGACY, headers=_FRESH)
    nf = _dist_file('404')
    if nf and not path.startswith(('api/', '_next/')):
        return FileResponse(nf, status_code=404, headers=_FRESH)
    return JSONResponse(status_code=404, content={'ok': False, 'error': f'not found: /{path}'})


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=PORT)
