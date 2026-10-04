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
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

import mod as m
from nycgis import layers as L
from nycgis import agentproto
from nycgis import mcp_server as mcp
from nycgis import scene
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


@app.get('/layers/population')
def population(metric: str = Query('density'), geography: str = Query('tract')):
    """Population density / census / housing-cost choropleth with class breaks."""
    out = nyc().population(metric=metric, geography=geography)
    if isinstance(out, dict) and out.get('error'):
        raise HTTPException(status_code=400, detail=out)
    return geo(out)


@app.get('/stats')
def stats(geography: str = Query('borough'), since: str = Query('2025-01-01'),
          sort: str = Query(''), limit: int = Query(0, ge=0, le=5000)):
    """Population and housing statistics per tract, neighborhood or borough."""
    try:
        return geo(nyc().stats(geography=geography, since=since, sort=sort, limit=limit))
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get('/report', response_class=HTMLResponse)
def report(since: str = Query('2025-01-01')):
    """The shareable brief: one self-contained HTML page — save it, email it, print it."""
    from nycgis import report as RP
    return HTMLResponse(RP.html_report(since), headers={'Cache-Control': LAYER_CACHE})


@app.get('/report.csv')
def report_csv(geography: str = Query('nta'), since: str = Query('2025-01-01')):
    """Every statistic as CSV, per tract, neighborhood or borough."""
    from nycgis import report as RP
    try:
        body = RP.csv(geography, since)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PlainTextResponse(body, media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename="nyc-{geography}-stats.csv"',
        'Cache-Control': LAYER_CACHE})


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
    'chat panel. '
    'YOU ALSO DRIVE THE USER\'S MAP. The user is looking at a live map beside '
    'this chat, and each message starts with what it shows right now. Whenever '
    'a question or request is about places, show it: call nyc_map to switch '
    'layers, set housing filters, dim, outline or hide areas (`only` keeps just '
    'the matching boroughs / neighborhoods), fly the camera, or '
    'draw any open dataset as an overlay (find it with nyc_find_datasets, read '
    'columns with nyc_dataset, then overlay with a SoQL where clause; prefer '
    'mode "areas" by zip with per_capita for comparisons, "heat" for density). '
    'When the answer has numbers, call nyc_infographic once with the headline '
    'stats, a ranked bar list and the sources. Requests like "only Brooklyn", '
    '"make it darker", "zoom into Harlem", "now by ZIP" are map edits — apply '
    'them with nyc_map against the current state and confirm in one line.')

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
    # Which agent answers: '' / 'claude' = the local Claude CLI below; any
    # other name is an agent on the agent protocol (see GET /agents).
    agent: Optional[str] = None
    # What the user's map shows right now, so "only Brooklyn" or "now by ZIP"
    # can be read against it. Sent by the page each turn; never trusted for
    # anything but prompt context.
    map_state: Optional[Dict[str, Any]] = None


# The display tools: their results are directives for the page, forwarded on
# the stream as `display` events once the tool has validated them.
DISPLAY_TOOLS = {'mcp__nyc__nyc_map', 'mcp__nyc__nyc_infographic'}


def _tool_result_json(block: dict) -> Optional[dict]:
    """The JSON a display tool returned, out of a stream-json tool_result."""
    if block.get('is_error'):
        return None
    content = block.get('content')
    texts = [content] if isinstance(content, str) else [
        c.get('text', '') for c in (content or []) if isinstance(c, dict)]
    for t in texts:
        try:
            out = json.loads(t)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(out, dict) and isinstance(out.get('directive'), dict):
            return out['directive']
    return None


@app.get('/overlay')
def overlay(spec: str):
    """GeoJSON for an agent overlay (the spec nyc_map validated), cached."""
    try:
        return scene.overlay_data(json.loads(spec))
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f'{type(e).__name__}: {e}')


# ── agents: who answers ASK NYC ──────────────────────────────────────────
#
# The Claude CLI is one answerer; every agent on the agent protocol is
# another. nycgis.agentproto is the only code that talks to that protocol.

CLAUDE_AGENT = 'claude'


@app.get('/agents')
def agents_list(q: str = ''):
    """
    Every agent that can answer: the Claude CLI (when installed) plus the
    agent protocol's roster, NYC agents first. nyc-atlas is filed on the
    protocol the first time anyone looks.
    """
    out: Dict[str, Any] = {'agents': [], 'protocol': None}
    if shutil.which('claude'):
        out['agents'].append({
            'name': CLAUDE_AGENT, 'label': 'Claude', 'icon': '?',
            'description': f'The local Claude CLI ({CHAT_MODEL}) on the nyc MCP tools',
            'nyc': True, 'builtin': True})
    try:
        out['registered'] = agentproto.register()
    except Exception as e:
        out['registered'] = {'error': f'{type(e).__name__}: {e}'}
    try:
        r = agentproto.roster(q)
        out['agents'] += r['agents']
        out['protocol'] = {k: r[k] for k in ('url', 'address', 'free', 'total')}
    except Exception as e:
        out['protocol'] = {'error': f'{type(e).__name__}: {e}',
                           'url': agentproto.agent_url()}
    out['default'] = (CLAUDE_AGENT if shutil.which('claude')
                      else agentproto.ATLAS)
    return out


class AgentMakeRequest(BaseModel):
    description: str
    name: Optional[str] = None


@app.post('/agents')
def agents_make(req: AgentMakeRequest):
    """
    Make a new NYC agent from one plain description, through the agent
    protocol (its vibe-builder drafts it; nyc then hands it the data tools).
    """
    try:
        return agentproto.create(req.description, req.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        # 4xx with the reason: the gateway strips 5xx bodies
        raise HTTPException(status_code=409, detail=str(e))


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
    agent = (req.agent or '').strip()
    if agent and agent != CLAUDE_AGENT:
        if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}', agent):
            raise HTTPException(status_code=400, detail='bad agent name')
        events = agentproto.run(agent, message, req.session_id, req.map_state)
        return StreamingResponse(
            (f'data: {json.dumps(ev)}\n\n' for ev in events),
            media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
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
    # an agent-protocol conversation id means nothing to the CLI
    if req.session_id and not req.session_id.startswith('nyc-'):
        cmd += ['--resume', req.session_id]
    if req.map_state:
        state = json.dumps(req.map_state, default=str)[:3000]
        message = f'[The user\'s map right now: {state}]\n\n{message}'

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
        display_ids: set = set()
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
                            if name in DISPLAY_TOOLS:
                                display_ids.add(block.get('id'))
                            if name.startswith('mcp__nyc__'):
                                yield sse({'type': 'tool',
                                           'name': name.replace('mcp__nyc__', ''),
                                           'input': block.get('input') or {}})
                elif t == 'user':
                    for block in (ev.get('message') or {}).get('content', []) or []:
                        if not isinstance(block, dict) or block.get('type') != 'tool_result':
                            continue
                        if block.get('tool_use_id') in display_ids:
                            d = _tool_result_json(block)
                            if d:
                                yield sse({'type': 'display', 'directive': d})
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
