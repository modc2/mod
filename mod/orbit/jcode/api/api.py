"""
jcode api — mod-protocol HTTP surface over the jcode coding-agent harness.

Wraps the jcode binary (headless `jcode run --ndjson`) and its on-disk state
(~/.jcode) behind a small FastAPI app:

    GET  /            module info (mod-protocol null call)
    GET  /health      liveness + binary/provider readiness
    GET  /version     `jcode version --json`
    GET  /auth        provider auth table, parsed
    GET  /usage       provider usage limits (raw text)
    GET  /providers   curated provider list with readiness flags
    GET  /sessions    saved sessions from ~/.jcode/sessions
    GET  /session/{id}  full transcript (session file + journal)
    GET  /stats       fleet-facing stats (sessions, tokens, crates, version)
    GET  /readme      project README
    GET  /changelog   changelog entries
    GET  /doc?name=   whitelisted docs/*.md
    POST /run         run a prompt through jcode; SSE stream of ndjson events

Tool-enabled runs (anything past --tool-profile none) are gated by the owner
token minted at ~/.mod/jcode/token.json — chat-only runs are open. Runs
default to the sandbox workspace ~/.mod/jcode/workspace.
"""

import asyncio
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

MODULE_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = Path.home() / '.mod' / 'jcode'
BIN = os.environ.get('JCODE_BIN', str(STATE_DIR / 'bin' / 'jcode'))
JCODE_HOME = Path(os.environ.get('JCODE_HOME', Path.home() / '.jcode'))
SESSIONS_DIR = JCODE_HOME / 'sessions'
WORKSPACE = STATE_DIR / 'workspace'
TOKEN_FILE = STATE_DIR / 'token.json'
RUN_TIMEOUT = int(os.environ.get('JCODE_RUN_TIMEOUT', '600'))
OPEN_TOOLS = os.environ.get('JCODE_TOOLS_OPEN') == '1'

PROVIDERS = [
    {'key': 'claude', 'label': 'Claude (OAuth / Max)', 'kind': 'oauth'},
    {'key': 'anthropic-api', 'label': 'Anthropic API', 'kind': 'api_key'},
    {'key': 'openai', 'label': 'ChatGPT / Codex (OAuth)', 'kind': 'oauth'},
    {'key': 'openai-api', 'label': 'OpenAI API', 'kind': 'api_key'},
    {'key': 'openrouter', 'label': 'OpenRouter', 'kind': 'api_key'},
    {'key': 'gemini', 'label': 'Gemini (OAuth)', 'kind': 'oauth'},
    {'key': 'ollama', 'label': 'Ollama (local)', 'kind': 'local'},
    {'key': 'lmstudio', 'label': 'LM Studio (local)', 'kind': 'local'},
]

app = FastAPI(title='jcode', description='mod-protocol API over the jcode harness')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'],
                   allow_headers=['*'])

_cache: dict = {}


def _env():
    env = dict(os.environ)
    env['JCODE_NO_TELEMETRY'] = '1'
    return env


def _jcode(*args, timeout=30):
    """Run the jcode CLI and return (rc, stdout, stderr)."""
    r = subprocess.run([BIN, *args, '--no-update', '--quiet'],
                       capture_output=True, text=True, timeout=timeout,
                       env=_env(), cwd=str(WORKSPACE))
    return r.returncode, r.stdout, r.stderr


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def _token():
    """Owner token for tool-enabled runs, minted on first use (off-tree)."""
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text())['token']
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    TOKEN_FILE.write_text(json.dumps({'token': token, 'minted_at': time.time()}))
    TOKEN_FILE.chmod(0o600)
    return token


def _authed(request: Request, body_token: str | None = None) -> bool:
    header = request.headers.get('authorization', '')
    supplied = header[7:] if header.lower().startswith('bearer ') else body_token
    return bool(supplied) and secrets.compare_digest(supplied, _token())


def _version():
    def load():
        try:
            rc, out, _ = _jcode('version', '--json')
            return json.loads(out) if rc == 0 else {}
        except Exception:
            return {}
    return _cached('version', 3600, load)


def _auth_rows():
    def load():
        try:
            rc, out, _ = _jcode('auth', 'status')
        except Exception:
            return []
        rows = []
        for line in out.splitlines():
            parts = [p.strip() for p in line.split('\t') if p.strip()]
            if len(parts) >= 2:
                rows.append({'provider': parts[0], 'status': parts[1],
                             'method': parts[2] if len(parts) > 2 else '',
                             'detail': parts[-1]})
        return rows
    return _cached('auth', 60, load)


def _session_meta(path: Path):
    try:
        d = json.loads(path.read_text())
    except Exception:
        return None
    messages = d.get('messages') or []
    return {
        'id': d.get('id') or path.stem,
        'title': d.get('title'),
        'short_name': d.get('short_name'),
        'model': d.get('model'),
        'provider': d.get('provider_key'),
        'status': d.get('status'),
        'working_dir': d.get('working_dir'),
        'created_at': d.get('created_at'),
        'updated_at': d.get('updated_at'),
        'messages': len(messages),
    }


def _normalize_content(content):
    """Session content is a string or a list of typed blocks — flatten both."""
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}]
    blocks = []
    for b in content if isinstance(content, list) else []:
        if not isinstance(b, dict):
            continue
        t = b.get('type', 'text')
        if t == 'text':
            blocks.append({'type': 'text', 'text': b.get('text', '')})
        elif t in ('tool_use', 'tool_call'):
            blocks.append({'type': 'tool_use', 'name': b.get('name'),
                           'input': b.get('input')})
        elif t == 'tool_result':
            out = b.get('content')
            if isinstance(out, list):
                out = ' '.join(x.get('text', '') for x in out if isinstance(x, dict))
            blocks.append({'type': 'tool_result', 'text': str(out)[:4000]})
        elif t == 'thinking':
            blocks.append({'type': 'thinking', 'text': b.get('thinking', '')[:4000]})
    return blocks


def _transcript(session_id: str):
    base = SESSIONS_DIR / f'{session_id}.json'
    if not base.exists():
        raise HTTPException(404, f'no session {session_id}')
    d = json.loads(base.read_text())
    seen, messages = set(), []

    def add(msg):
        if not isinstance(msg, dict) or msg.get('id') in seen:
            return
        seen.add(msg.get('id'))
        messages.append({
            'id': msg.get('id'),
            'role': msg.get('display_role') or msg.get('role'),
            'timestamp': msg.get('timestamp'),
            'content': _normalize_content(msg.get('content')),
        })

    for msg in d.get('messages') or []:
        add(msg)
    journal = SESSIONS_DIR / f'{session_id}.journal.jsonl'
    if journal.exists():
        for line in journal.read_text().splitlines():
            try:
                entry = json.loads(line)
            except Exception:
                continue
            for msg in entry.get('append_messages') or []:
                add(msg)
    meta = _session_meta(base) or {}
    meta['transcript'] = messages
    return meta


# ---- routes ----

@app.get('/')
def root():
    return info()


@app.get('/info')
def info():
    v = _version()
    return {
        'name': 'jcode',
        'description': 'The most RAM-efficient coding-agent harness, on the mod protocol',
        'version': v.get('semver'),
        'binary': BIN,
        'urls': {'api': 'http://localhost:50330', 'app': 'http://localhost:50331'},
        'endpoints': ['/health', '/version', '/auth', '/usage', '/providers',
                      '/sessions', '/session/{id}', '/stats', '/readme',
                      '/changelog', '/doc', '/run'],
    }


@app.get('/health')
def health():
    ready = [r['provider'] for r in _auth_rows() if r['status'] not in
             ('not_configured', 'error')]
    return {'ok': Path(BIN).exists(), 'binary': Path(BIN).exists(),
            'version': _version().get('semver'), 'providers_ready': ready}


@app.get('/version')
def version():
    return _version()


@app.get('/auth')
def auth():
    return {'providers': _auth_rows()}


@app.get('/usage')
def usage():
    def load():
        try:
            rc, out, err = _jcode('usage', timeout=60)
            return {'ok': rc == 0, 'raw': out or err}
        except Exception as e:
            return {'ok': False, 'raw': str(e)}
    return _cached('usage', 300, load)


@app.get('/providers')
def providers():
    status = {r['provider']: r['status'] for r in _auth_rows()}
    return {'providers': [
        {**p, 'status': status.get(p['key'], 'unknown'),
         'ready': status.get(p['key'], '') not in ('not_configured', 'error', 'unknown')}
        for p in PROVIDERS]}


@app.get('/sessions')
def sessions(limit: int = 50):
    if not SESSIONS_DIR.exists():
        return {'sessions': []}
    metas = [m for m in (_session_meta(p) for p in SESSIONS_DIR.glob('session_*.json')) if m]
    metas.sort(key=lambda s: s.get('updated_at') or '', reverse=True)
    return {'sessions': metas[:limit], 'total': len(metas)}


@app.get('/session/{session_id}')
def session(session_id: str):
    if '/' in session_id or '..' in session_id:
        raise HTTPException(400, 'bad session id')
    return _transcript(session_id)


@app.get('/stats')
def stats():
    def load():
        crates = len(list((MODULE_DIR / 'crates').glob('jcode-*'))) \
            if (MODULE_DIR / 'crates').exists() else 0
        n_sessions = len(list(SESSIONS_DIR.glob('session_*.json'))) \
            if SESSIONS_DIR.exists() else 0
        return {'version': _version().get('semver'), 'crates': crates,
                'sessions': n_sessions, 'ram_baseline_mb': 27.8}
    return _cached('stats', 120, load)


@app.get('/readme', response_class=PlainTextResponse)
def readme():
    for name in ('README.md', 'readme.md'):
        p = MODULE_DIR / name
        if p.exists():
            return p.read_text()
    raise HTTPException(404, 'no readme')


@app.get('/changelog')
def changelog(limit: int = 20):
    d = MODULE_DIR / 'changelog'
    if not d.exists():
        return {'entries': []}
    files = sorted(d.glob('*.md'), reverse=True)[:limit]
    return {'entries': [{'name': p.stem, 'text': p.read_text()[:8000]} for p in files]}


@app.get('/doc', response_class=PlainTextResponse)
def doc(name: str):
    docs = (MODULE_DIR / 'docs').resolve()
    p = (docs / name).resolve()
    if not str(p).startswith(str(docs)) or not p.suffix == '.md' or not p.exists():
        raise HTTPException(404, f'no doc {name}')
    return p.read_text()


@app.get('/docs-list')
def docs_list():
    d = MODULE_DIR / 'docs'
    return {'docs': sorted(p.name for p in d.glob('*.md')) if d.exists() else []}


class RunBody(BaseModel):
    prompt: str
    provider: str = 'claude'
    model: str | None = None
    tools: str = 'none'          # none | lite | full
    cwd: str | None = None
    resume: str | None = None
    token: str | None = None


@app.post('/run')
async def run(body: RunBody, request: Request):
    """Stream a headless jcode run as SSE (one ndjson event per message)."""
    if not body.prompt.strip():
        raise HTTPException(400, 'empty prompt')
    if len(body.prompt) > 100_000:
        raise HTTPException(400, 'prompt too long')
    if body.tools != 'none' and not OPEN_TOOLS and not _authed(request, body.token):
        raise HTTPException(403, 'tool-enabled runs need the owner token '
                                 '(m jcode/token) as a Bearer header')
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    cwd = body.cwd or str(WORKSPACE)
    if not Path(cwd).is_dir():
        raise HTTPException(400, f'cwd {cwd} is not a directory')

    args = [BIN, 'run', body.prompt, '-p', body.provider, '--ndjson',
            '--quiet', '--no-update', '-C', cwd,
            '--tool-profile', {'lite': 'lite', 'full': 'full'}.get(body.tools, 'none')]
    if body.model:
        args += ['-m', body.model]
    if body.resume:
        args += ['--resume', body.resume]

    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=_env(), cwd=cwd)

    async def events():
        try:
            deadline = time.monotonic() + RUN_TIMEOUT
            while True:
                budget = deadline - time.monotonic()
                if budget <= 0:
                    yield 'data: {"type":"error","message":"run timeout"}\n\n'
                    break
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), budget)
                except asyncio.TimeoutError:
                    yield 'data: {"type":"error","message":"run timeout"}\n\n'
                    break
                if not line:
                    break
                text = line.decode(errors='replace').strip()
                if text:
                    yield f'data: {text}\n\n'
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), 5)
                except asyncio.TimeoutError:
                    proc.kill()
            if proc.returncode not in (0, None):
                err = (await proc.stderr.read()).decode(errors='replace')[-2000:]
                yield ('data: ' + json.dumps(
                    {'type': 'error', 'message': err.strip() or f'exit {proc.returncode}'})
                    + '\n\n')
            yield 'data: {"type":"eof"}\n\n'
        finally:
            if proc.returncode is None:
                proc.kill()

    return StreamingResponse(events(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache',
                                      'X-Accel-Buffering': 'no'})
