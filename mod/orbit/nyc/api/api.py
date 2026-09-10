"""
nyc API — FastAPI wrapper over the nyc mod.

Serves the layer catalogue and every map layer as GeoJSON. Launched/killed via
``mod.py serve_api() / kill()``.

GeoJSON is verbose and highly repetitive, so responses are gzipped — the bike
network drops from ~6 MB to well under 1 MB on the wire. Layer responses are
also given a long browser cache lifetime, since the underlying open data
updates daily at most.
"""

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import mod as m
from nycgis import layers as L
from nycgis import mcp_server as mcp
from nycgis import tools
from nycgis.mcp_server import INSTRUCTIONS, PROTOCOL_VERSION, SERVER_INFO

_nyc = None


def nyc():
    global _nyc
    if _nyc is None:
        _nyc = m.mod('nyc')()
    return _nyc


app = FastAPI(
    title='NYC GIS API',
    description=('Open-source GIS for New York City — housing prices, transit, '
                 'parks and civic data as GeoJSON layers. All sources are public '
                 'open data; no API keys anywhere.'),
    version='1.0.0')

app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True,
                   allow_methods=['*'], allow_headers=['*'],
                   # A browser MCP client cannot read the session it was just
                   # issued unless the header is explicitly exposed to it.
                   expose_headers=['Mcp-Session-Id', 'MCP-Protocol-Version'])

# Layer geometry changes daily at most; let the browser hold onto it.
LAYER_CACHE = 'public, max-age=3600, stale-while-revalidate=86400'


def geo(payload: dict, cache: str = LAYER_CACHE) -> JSONResponse:
    return JSONResponse(payload, headers={'Cache-Control': cache})


@app.get('/')
def root():
    return nyc().info()


@app.get('/health')
def health():
    return nyc().health()


@app.get('/view')
def view():
    """Default map camera + borough quick-jump targets."""
    return nyc().view()


@app.get('/layers')
def layers():
    """The layer catalogue that drives the UI's layer panel."""
    return geo(nyc().layers(), cache='public, max-age=300')


@app.get('/options')
def options():
    """Metrics, geographies and property types for the housing controls."""
    return geo(nyc().options(), cache='public, max-age=3600')


@app.get('/layers/housing_prices')
def housing_prices(
    metric: str = Query('median_price'),
    geography: str = Query('nta'),
    since: str = Query('2024-01-01'),
    until: Optional[str] = Query(None),
    property_type: str = Query('residential'),
):
    """A housing-price choropleth as GeoJSON, with quantile class breaks."""
    out = nyc().housing(metric=metric, geography=geography, since=since,
                        until=until, property_type=property_type)
    if isinstance(out, dict) and out.get('error'):
        raise HTTPException(status_code=400, detail=out)
    return geo(out)


@app.get('/layers/sales')
def sales(
    since: str = Query('2025-01-01'),
    until: Optional[str] = Query(None),
    property_type: str = Query('residential'),
    limit: int = Query(12000, ge=1, le=50000),
    min_price: Optional[int] = Query(None),
    max_price: Optional[int] = Query(None),
):
    """Individual recorded sales as points."""
    return geo(nyc().sales(since=since, until=until, property_type=property_type,
                           limit=limit, min_price=min_price, max_price=max_price))


@app.get('/layers/{layer_id}')
def layer(layer_id: str):
    """Any catalogue layer as a GeoJSON FeatureCollection."""
    try:
        # Live layers (traffic speeds) carry their own, much shorter lifetime.
        return geo(nyc().layer(layer_id),
                   cache=L.cache_control(layer_id, LAYER_CACHE))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502,
                            detail=f'upstream source failed: {type(e).__name__}: {e}')


@app.get('/boundary/{name}')
def boundary(name: str):
    try:
        return geo(nyc().boundary(name))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get('/prices')
def prices(since: str = Query('2024-01-01'), until: Optional[str] = Query(None),
           property_type: str = Query('residential')):
    """City-wide price summary: totals, most/least expensive, biggest movers."""
    return geo(nyc().prices(since=since, until=until, property_type=property_type),
               cache='public, max-age=3600')


@app.get('/traffic')
def traffic(street: str = Query(''), borough: str = Query(''),
            hour: Optional[int] = Query(None, ge=0, le=23),
            limit: int = Query(20, ge=1, le=200)):
    """When to drive: hourly volume profiles plus the live speed picture."""
    return geo(nyc().traffic(street=street, borough=borough, hour=hour,
                             limit=limit),
               cache='public, max-age=180')


@app.get('/rents')
def rents():
    """What affordable homes rent for: medians by bedroom, income band, borough."""
    return geo(nyc().rents(), cache='public, max-age=3600')


@app.get('/homes')
def homes(max_rent: Optional[int] = Query(None, ge=0),
          bedrooms: str = Query(''), borough: str = Query(''),
          search: str = Query(''), limit: int = Query(200, ge=1, le=5000)):
    """Affordable rentals matching a budget and household size, cheapest first."""
    return geo(nyc().homes(max_rent=max_rent, bedrooms=bedrooms,
                           borough=borough, search=search, limit=limit),
               cache='public, max-age=3600')


@app.get('/affordable')
def affordable():
    """Affordable units built, tallied by income band and borough."""
    return geo(nyc().affordable(), cache='public, max-age=3600')


@app.get('/trend')
def trend(area: Optional[str] = Query(None), geography: str = Query('nta'),
          property_type: str = Query('residential'),
          start_year: int = Query(2016, ge=2016, le=2100)):
    """Yearly median price / $ per ft² — city-wide, or for one area."""
    return geo(nyc().trend(area=area, geography=geography,
                           property_type=property_type, start_year=start_year),
               cache='public, max-age=3600')


@app.get('/where')
def where(q: str = Query(..., min_length=1), limit: int = Query(6, ge=1, le=20)):
    """Geocode an address or place within NYC (OpenStreetMap Nominatim)."""
    try:
        return nyc().where(q, limit=limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'geocoder failed: {e}')


@app.get('/cache')
def cache():
    return nyc().cache()


# ─────────────────────────────────────────────────────────────── tools + MCP

@app.get('/tools')
def tools_list():
    """
    The whole MCP surface as plain JSON — tools, prompts and resources, from
    the same registry both transports serve. The docs page renders itself from
    this, so a tool added to the registry documents itself.
    """
    return geo({
        'count': len(tools.TOOLS),
        'groups': tools.groups(),
        'tools': tools.list_tools(),
        'prompts': mcp._prompt_list(),
        'resources': mcp._resources(),
        'server': mcp.SERVER_INFO,
        'instructions': INSTRUCTIONS,
        'mcp': {'http': '/nyc/api/mcp',
                'stdio': 'python3 -m nycgis.mcp_server',
                'protocol': PROTOCOL_VERSION,
                'supported': list(mcp.SUPPORTED_PROTOCOLS),
                'capabilities': mcp.CAPABILITIES},
    }, cache='public, max-age=300')


@app.post('/tools/{name}')
async def tools_call(name: str, request: Request):
    """Call one tool with a JSON object of arguments."""
    try:
        args = await request.json()
    except Exception:
        args = {}
    if not isinstance(args, dict):
        raise HTTPException(status_code=400, detail='arguments must be a JSON object')
    try:
        return {'ok': True, 'tool': name, 'result': tools.call_tool(name, args)}
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        return JSONResponse(status_code=400, content={
            'ok': False, 'tool': name, 'error': f'{type(e).__name__}: {e}'})


# ── MCP streamable HTTP ──────────────────────────────────────────────────
#
# The JSON-RPC dispatch itself lives in nycgis.mcp_server and is shared with
# the stdio transport; everything here is HTTP framing around it. Sessions are
# tracked so a client that is handed an id gets told when it has gone stale
# (a restarted API), but a client that sends no id at all is still served —
# every tool is read-only and stateless, so there is nothing to protect.

MCP_SESSIONS: set[str] = set()


def _mcp_headers(session: Optional[str] = None) -> dict:
    h = {'MCP-Protocol-Version': PROTOCOL_VERSION}
    if session:
        h['Mcp-Session-Id'] = session
    return h


def _sse(payloads: list) -> StreamingResponse:
    """One JSON-RPC reply per SSE event, for clients that ask for a stream."""
    def gen():
        for p in payloads:
            yield f'data: {json.dumps(p)}\n\n'
    return StreamingResponse(gen(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache',
                                      'X-Accel-Buffering': 'no'})


@app.post('/mcp')
async def mcp_post(request: Request):
    """MCP streamable-HTTP endpoint — same JSON-RPC surface as the stdio server."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            'jsonrpc': '2.0', 'id': None,
            'error': {'code': -32700, 'message': 'parse error'}})

    # A session id we never issued means the client is talking to a different
    # process than the one it initialised against — usually an API restart.
    sent = request.headers.get('mcp-session-id')
    if sent and sent not in MCP_SESSIONS:
        return JSONResponse(status_code=404, headers=_mcp_headers(), content={
            'jsonrpc': '2.0', 'id': None,
            'error': {'code': -32001, 'message': 'unknown session; reinitialize'}})

    msgs = body if isinstance(body, list) else [body]
    if not all(isinstance(x, dict) for x in msgs):
        return JSONResponse(status_code=400, content={
            'jsonrpc': '2.0', 'id': None,
            'error': {'code': -32600, 'message': 'invalid request'}})

    session = sent
    if any(x.get('method') == 'initialize' for x in msgs):
        session = uuid.uuid4().hex
        MCP_SESSIONS.add(session)

    replies = [r for r in (mcp.handle_message(x) for x in msgs) if r is not None]

    # Notifications only: nothing to answer, and 202 with an empty body is what
    # the spec asks for — a JSON `null` here trips strict clients.
    if not replies:
        return Response(status_code=202, headers=_mcp_headers(session))

    if 'text/event-stream' in request.headers.get('accept', '') \
            and 'application/json' not in request.headers.get('accept', ''):
        return _sse(replies)

    payload = replies if isinstance(body, list) else replies[0]
    return JSONResponse(payload, headers=_mcp_headers(session))


@app.delete('/mcp')
def mcp_delete(request: Request):
    """End a session. Nothing is stored against it, so this is bookkeeping."""
    MCP_SESSIONS.discard(request.headers.get('mcp-session-id') or '')
    return Response(status_code=204, headers=_mcp_headers())


@app.get('/mcp')
def mcp_get():
    """No server-initiated stream: the spec's answer for that is a plain 405."""
    return JSONResponse(status_code=405, headers=_mcp_headers(), content={
        'error': 'POST JSON-RPC here (MCP streamable HTTP); '
                 'this server opens no server-initiated SSE stream'})


# ─────────────────────────────────────────────────────────────────── chat

MODULE_DIR = Path(__file__).parent.parent
MOD_ROOT = MODULE_DIR.parent.parent.parent
CHAT_MODEL = os.environ.get('NYC_CHAT_MODEL', 'sonnet')
CHAT_TIMEOUT = int(os.environ.get('NYC_CHAT_TIMEOUT', '240'))

CHAT_SYSTEM = (
    'You are the NYC Atlas analyst — a data agent for New York City, built '
    'on public open data and aimed at city staff and residents alike. Answer '
    'questions about NYC with the nyc_* tools; never guess a number you '
    'could look up. Housing questions: nyc_housing / nyc_prices / nyc_trend '
    '/ nyc_sales. Transit, parks, flood zones, crashes: nyc_layers + '
    'nyc_layer. Anything else (311, crime, schools, health, budgets, '
    'permits): nyc_find_datasets → nyc_dataset → nyc_query. Keep answers '
    'short and concrete: lead with the figure, name the neighborhood, and '
    'cite the dataset it came from. Plain text only — no markdown tables, '
    'no headers; short paragraphs and simple "-" lists render best in the '
    'chat panel.')

# Tools the headless agent may touch: our MCP server, nothing else. The CLI
# denies everything outside this list, so the chat agent cannot reach the
# filesystem or shell even though it runs server-side.
CHAT_ALLOWED = 'mcp__nyc'
CHAT_DENIED = ('Bash,Edit,Write,NotebookEdit,Read,Glob,Grep,WebFetch,'
               'WebSearch,Task,TodoWrite')


def _chat_mcp_config() -> str:
    """Write the MCP config the CLI points at; per-user state, so off-tree."""
    cfg_dir = Path(os.path.expanduser('~/.mod/nyc'))
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / 'mcp.json'
    cfg.write_text(json.dumps({'mcpServers': {'nyc': {
        'command': sys.executable or 'python3',
        'args': ['-m', 'nycgis.mcp_server'],
        'cwd': str(MODULE_DIR),
        'env': {'PYTHONPATH': str(MOD_ROOT)},
    }}}, indent=2))
    return str(cfg)


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


@app.get('/chat/health')
def chat_health():
    cli = shutil.which('claude')
    return {'available': bool(cli), 'cli': cli, 'model': CHAT_MODEL,
            'tools': len(tools.TOOLS)}


@app.post('/chat')
def chat(req: ChatRequest):
    """
    Ask the NYC agent a question. Streams SSE events:

        session {id}            the conversation id (pass back as session_id)
        tool    {name, input}   the agent consulting a data tool
        text    {text}          a block of the answer
        done    {ms}            end of turn
        error   {error}

    The agent is the local Claude CLI in print mode, sandboxed to this
    module's MCP tools — it can read every open dataset and nothing else.
    """
    message = (req.message or '').strip()
    if not message:
        raise HTTPException(status_code=400, detail='empty message')
    if len(message) > 4000:
        raise HTTPException(status_code=400, detail='message too long (4000 chars)')
    if not shutil.which('claude'):
        raise HTTPException(status_code=503,
                            detail='claude CLI not installed on this host')

    cmd = ['claude', '-p', '--output-format', 'stream-json', '--verbose',
           '--model', CHAT_MODEL,
           '--strict-mcp-config', '--mcp-config', _chat_mcp_config(),
           '--allowedTools', CHAT_ALLOWED,
           '--disallowedTools', CHAT_DENIED,
           '--append-system-prompt', CHAT_SYSTEM,
           '--max-turns', '25']
    if req.session_id:
        cmd += ['--resume', req.session_id]

    def sse(event: dict) -> str:
        return f'data: {json.dumps(event)}\n\n'

    def stream():
        import threading
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=str(MODULE_DIR))
        # A hung agent must not pin the connection open forever.
        watchdog = threading.Timer(CHAT_TIMEOUT, proc.kill)
        watchdog.start()
        try:
            proc.stdin.write(message)
            proc.stdin.close()
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = ev.get('type')
                if t == 'system' and ev.get('subtype') == 'init':
                    yield sse({'type': 'session', 'id': ev.get('session_id')})
                elif t == 'assistant':
                    for block in (ev.get('message') or {}).get('content', []):
                        if block.get('type') == 'text' and block.get('text'):
                            yield sse({'type': 'text', 'text': block['text']})
                        elif block.get('type') == 'tool_use':
                            # Only surface real data tools — the CLI also emits
                            # harness plumbing (ToolSearch) nobody needs to see.
                            name = str(block.get('name', ''))
                            if name.startswith('mcp__nyc__'):
                                yield sse({'type': 'tool',
                                           'name': name.replace('mcp__nyc__', ''),
                                           'input': block.get('input') or {}})
                elif t == 'result':
                    err = ev.get('subtype') != 'success'
                    out = {'type': 'done', 'ms': ev.get('duration_ms'),
                           'session_id': ev.get('session_id')}
                    if err:
                        out = {'type': 'error',
                               'error': ev.get('result') or ev.get('subtype')}
                    yield sse(out)
            rc = proc.wait(timeout=10)
            if rc != 0:
                tail = (proc.stderr.read() or '')[-400:]
                yield sse({'type': 'error', 'error': f'agent exited {rc}: {tail}'})
        except GeneratorExit:
            # client went away mid-answer — don't leave the agent running
            proc.kill()
            raise
        except Exception as e:
            yield sse({'type': 'error', 'error': f'{type(e).__name__}: {e}'})
        finally:
            watchdog.cancel()
            if proc.poll() is None:
                proc.kill()

    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache',
                                      'X-Accel-Buffering': 'no'})


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 50310)))
