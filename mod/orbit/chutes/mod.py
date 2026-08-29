"""chutes — one 8-bit cabinet over chutes.ai serverless GPU inference.

The backend is chutes-rs/ (axum): an MCP server (JSON-RPC 2.0 over Streamable
HTTP at /mcp, plus --stdio for MCP clients like Claude Code). Every REST route
on the server dispatches through the same MCP tool layer, and this Python mod
is a thin client over that server — with a direct-to-chutes.ai fallback when
the server isn't running.
"""

import os
import json
import subprocess
import itertools
import requests


# Mirrors chutes-rs/src/chutes.rs — keep the two in step.
BASE = 'https://api.chutes.ai'
CHAT_PATH = '/v1/chat/completions'
IMAGES_PATH = '/v1/images/generations'
ENV_KEY = 'CHUTES_API_KEY'
DEFAULT_MODEL = 'Qwen/Qwen3-32B-TEE'
KEY_FILE = '~/.mod/chutes/api_key'


def _box_defaults():
    """Box-local defaults — ~/.mod/chutes/defaults.json, the same file the Rust
    backend reads: {"models": "id" | ["id", …]}."""
    path = os.path.expanduser('~/.mod/chutes/defaults.json')
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _default_models():
    """The default chute plus stand-ins: env → defaults.json → DEFAULT_MODEL."""
    env = os.environ.get('CHUTES_DEFAULT_MODEL', '').strip()
    if env:
        return [env]
    configured = _box_defaults().get('models')
    if isinstance(configured, dict):          # legacy {"models": {"chutes": …}}
        configured = configured.get('chutes')
    if isinstance(configured, str):
        configured = [configured]
    out = [str(m).strip() for m in (configured or []) if str(m).strip()]
    return out or [DEFAULT_MODEL]


def _resolve_key():
    """The server-side key, or ''. Mirrors chutes.rs:
    env → ~/.mod/chutes/api_key → ~/.mod/chutes/key.json {key} →
    ~/.mod/model/chutes/apikeys.json [key, …] (the model mod's shape)."""
    env = os.environ.get(ENV_KEY, '').strip()
    if env:
        return env, 'env'
    try:
        with open(os.path.expanduser(KEY_FILE)) as f:
            k = f.read().strip()
        if k:
            return k, 'api_key'
    except OSError:
        pass
    for path, fields in (('~/.mod/chutes/key.json', ('key', 'api_key')),
                         ('~/.mod/model/chutes/apikeys.json', None)):
        try:
            with open(os.path.expanduser(path)) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        found = ''
        if fields and isinstance(data, dict):
            found = next((str(data[f]).strip() for f in fields if data.get(f)), '')
        elif isinstance(data, list):
            found = next((str(k).strip() for k in data if str(k).strip()), '')
        if found:
            return found, os.path.basename(path)
    return '', 'none'


class Mod:
    description = """
    chutes.ai serverless GPU inference behind one Rust MCP server (chutes-rs) —
    chat, image generation, a price router over the chute catalog and chute
    management, as MCP tools over Streamable HTTP (/mcp) and stdio, plus an
    8-bit browser console.
    """

    def __init__(self, api_key: str = None, default_model: str = None,
                 base_url: str = None, server_url: str = None, **kwargs):
        self.dir = os.path.dirname(os.path.abspath(__file__))
        # An explicitly-passed key rides as x-api-key; a key resolved off disk
        # is the server's own business and is never re-sent.
        self.api_key = api_key or self.key()
        self._api_key_explicit = bool(api_key)
        self.default_model = default_model or _default_models()[0]
        self.base_url = base_url or os.environ.get('CHUTES_BASE_URL', BASE)
        cfg = self._load_config()
        self.port = cfg.get('port', 50300)
        self.server_url = server_url or f'http://127.0.0.1:{self.port}'
        self._rpc_ids = itertools.count(1)

    # ── config / secrets ─────────────────────────────────────────

    def _load_config(self):
        cfg_path = os.path.join(self.dir, 'config.json')
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                return json.load(f)
        return {}

    def key(self):
        """The resolved chutes key (see _resolve_key for the search order)."""
        return _resolve_key()[0]

    def keys(self):
        """Whether a key resolves, and from where — never the key itself."""
        src = _resolve_key()[1]
        return {'key': src != 'none', 'source': src, 'env': ENV_KEY, 'file': KEY_FILE}

    def set_api_key(self, api_key: str, persist: bool = False):
        """Set the key; persist=True writes ~/.mod/chutes/api_key (0600)."""
        self.api_key = api_key
        self._api_key_explicit = True
        if persist:
            path = os.path.expanduser(KEY_FILE)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w') as f:
                f.write(api_key)
            os.chmod(path, 0o600)
        return {'status': 'set', 'persisted': persist}

    # ── MCP client (the backend) ─────────────────────────────────

    def _server_up(self):
        try:
            r = requests.get(f'{self.server_url}/health', timeout=2)
            return r.ok
        except Exception:
            return False

    def _headers(self):
        headers = {'Content-Type': 'application/json'}
        if self.api_key and self._api_key_explicit:
            headers['x-chutes-key'] = self.api_key
        return headers

    def mcp_call(self, tool: str, arguments: dict = None, **kwargs):
        """Call an MCP tool on the Rust backend (JSON-RPC tools/call)."""
        arguments = dict(arguments or {}, **kwargs)
        resp = requests.post(
            f'{self.server_url}/mcp',
            json={
                'jsonrpc': '2.0',
                'id': next(self._rpc_ids),
                'method': 'tools/call',
                'params': {'name': tool, 'arguments': arguments},
            },
            headers=self._headers(), timeout=180,
        )
        resp.raise_for_status()
        msg = resp.json()
        if 'error' in msg:
            raise RuntimeError(msg['error'].get('message', str(msg['error'])))
        result = msg.get('result', {})
        if result.get('isError'):
            raise RuntimeError(result.get('content', [{}])[0].get('text', 'tool error'))
        if 'structuredContent' in result:
            return result['structuredContent']
        text = result.get('content', [{}])[0].get('text', '')
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return text

    def mcp_config(self):
        """MCP client config snippets (stdio + Streamable HTTP)."""
        binary = os.path.join(self.dir, 'chutes-rs', 'target', 'release', 'chutes-api')
        return {
            'stdio': {'command': binary, 'args': ['--stdio'], 'env': {ENV_KEY: '<your key>'}},
            'http': {'url': f'{self.server_url}/mcp'},
            'claude_code': f'claude mcp add chutes -- {binary} --stdio',
        }

    # ── direct fallback (server down) ────────────────────────────

    def _direct(self, method, path, body=None, params=None):
        headers = {'Content-Type': 'application/json'}
        key = self.api_key or self.key()
        if key:
            headers['Authorization'] = f'Bearer {key}'
        resp = requests.request(method, f'{self.base_url}{path}', json=body,
                                params=params, headers=headers, timeout=180)
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def _call(self, tool, direct, **arguments):
        """Route through the MCP server; fall back to chutes.ai directly."""
        if self._server_up():
            return self.mcp_call(tool, arguments)
        return direct()

    # ── api surface ──────────────────────────────────────────────

    def forward(self, message: str = None, model: str = None,
                system_prompt: str = None, **kwargs):
        """Default entry — simple chat completion (or status if empty)."""
        if not message:
            return self.status()
        result = self.chat(
            [{'role': 'user', 'content': message}], model=model,
            system=system_prompt or kwargs.pop('system', None), **kwargs)
        try:
            return result['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            return result

    def status(self, counts: bool = False):
        """Base URL, default chute (+ stand-ins) and whether a key resolves."""
        def direct():
            return dict(id='chutes', base_url=self.base_url, default_model=self.default_model,
                        default_models=_default_models(), **self.keys())
        return self._call('status', direct, counts=counts)

    def chat(self, messages, model=None, system=None,
             temperature: float = 0.7, max_tokens: int = 4096, **kwargs):
        """OpenAI-compatible chat completion on a chute."""
        if isinstance(messages, str):
            messages = [{'role': 'user', 'content': messages}]
        args = {'messages': messages, 'temperature': temperature, 'max_tokens': max_tokens}
        if model:
            args['model'] = model
        if system:
            args['system'] = system

        def direct():
            body = {'model': model or self.default_model, 'messages': messages,
                    'temperature': temperature, 'max_tokens': max_tokens, 'stream': False}
            if system:
                body['messages'] = [{'role': 'system', 'content': system}] + messages
            return self._direct('POST', CHAT_PATH, body)

        return self._call('chat', direct, **args)

    def compare(self, message: str, models: list = None, system: str = None,
                max_tokens: int = 1024, **kwargs):
        """Race one prompt across chutes; returns text + latency + cost each."""
        args = {'message': message, 'max_tokens': max_tokens}
        if models:
            args['models'] = models
        if system:
            args['system'] = system

        def direct():
            out = []
            for m in (models or _default_models()):
                try:
                    r = self.chat(message, model=m, system=system, max_tokens=max_tokens)
                    out.append({'model': m, 'text': r['choices'][0]['message']['content']})
                except Exception as e:
                    out.append({'model': m, 'error': str(e)})
            return {'results': out}

        return self._call('compare', direct, **args)

    def route(self, search: str = None, kind: str = 'chat', max_price: float = None,
              sort: str = 'price', limit: int = 10, ask: str = None, **kwargs):
        """Rank chutes by price/invocations; optionally run `ask` on the winner."""
        args = {'kind': kind, 'sort': sort, 'limit': limit}
        for k, v in (('search', search), ('max_price', max_price), ('ask', ask)):
            if v is not None:
                args[k] = v
        return self._call('route', lambda: self.models(search=search), **args)

    def models(self, search: str = None, kind: str = 'any', sort: str = 'price',
               limit: int = 200, refresh: bool = False):
        """The chute catalog, normalized with USD/1M prices."""
        args = {'kind': kind, 'sort': sort, 'limit': limit, 'refresh': refresh}
        if search:
            args['search'] = search

        def direct():
            data = self._direct('GET', '/chutes/', params={'page': 0, 'limit': limit})
            items = data.get('items', [])
            if search:
                q = search.lower()
                items = [c for c in items if q in json.dumps(c).lower()]
            return {'items': items, 'total': len(items)}

        return self._call('models', direct, **args)

    def generate_image(self, prompt: str, model: str = None,
                       size: str = '1024x1024', n: int = 1, **kwargs):
        """Generate images on a diffusion chute via the MCP `generate_image` tool."""
        args = {'prompt': prompt, 'size': size, 'n': n}
        if model:
            args['model'] = model

        def direct():
            return self._direct('POST', IMAGES_PATH, dict(args, response_format='url'))

        return self._call('generate_image', direct, **args)

    # ── chutes.ai control plane ──────────────────────────────────

    def list_chutes(self, page: int = 1, limit: int = 50):
        return self._call(
            'list_chutes',
            lambda: self._direct('GET', '/chutes/', params={'page': page, 'limit': limit}),
            page=page, limit=limit)

    def get_chute(self, chute_id: str):
        return self._call('get_chute', lambda: self._direct('GET', f'/chutes/{chute_id}'),
                          chute_id=chute_id)

    def deploy_chute(self, config: dict):
        return self._call('deploy_chute', lambda: self._direct('POST', '/chutes/', config),
                          config=config)

    def delete_chute(self, chute_id: str):
        return self._call('delete_chute', lambda: self._direct('DELETE', f'/chutes/{chute_id}'),
                          chute_id=chute_id)

    def warmup(self, chute_id: str):
        return self._call('warmup', lambda: self._direct('GET', f'/chutes/warmup/{chute_id}'),
                          chute_id=chute_id)

    def utilization(self):
        return self._call('utilization', lambda: self._direct('GET', '/chutes/utilization'))

    # ── build / serve / kill ─────────────────────────────────────

    @property
    def binary(self):
        return os.path.join(self.dir, 'chutes-rs', 'target', 'release', 'chutes-api')

    def build(self, **kwargs):
        """Build the Rust MCP server (cargo build --release)."""
        rs_dir = os.path.join(self.dir, 'chutes-rs')
        result = subprocess.run(
            ['cargo', 'build', '--release'],
            cwd=rs_dir, capture_output=True, text=True,
            env={**os.environ, 'PATH': os.environ['PATH'] + ':' + os.path.expanduser('~/.cargo/bin')},
        )
        if result.returncode != 0:
            return {'status': 'build_failed', 'stderr': result.stderr[-3000:]}
        return {'status': 'built', 'binary': self.binary}

    def serve(self, port=None, **kwargs):
        """Run the Rust MCP server under pm2 as chutes-api."""
        port = port or self.port
        if not os.path.exists(self.binary):
            built = self.build()
            if built.get('status') != 'built':
                return built
        self.kill()
        env = {**os.environ, 'PORT': str(port)}
        k = self.key()
        if k:
            env[ENV_KEY] = k
        subprocess.run(
            ['pm2', 'start', self.binary, '--name', 'chutes-api'],
            cwd=self.dir, env=env, capture_output=True)
        return {
            'api': f'http://localhost:{port}',
            'mcp': f'http://localhost:{port}/mcp',
            'console': f'http://localhost:{port}/ (browser)',
            'processes': ['chutes-api'],
        }

    def kill(self, **kwargs):
        """Stop the chutes backend (current and legacy pm2 names)."""
        killed = []
        for name in ['chutes-api', 'chutes.api', 'chutes.app']:
            r = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    # ── test ─────────────────────────────────────────────────────

    def test(self, **kwargs):
        """Connectivity test: server health, MCP handshake, catalog, key, chat."""
        results = {'server_url': self.server_url, 'server_up': self._server_up()}

        if results['server_up']:
            try:
                r = requests.post(
                    f'{self.server_url}/mcp',
                    json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                          'params': {'protocolVersion': '2025-06-18'}},
                    timeout=5).json()
                results['mcp_initialize'] = r.get('result', {}).get('serverInfo')
                r = requests.post(
                    f'{self.server_url}/mcp',
                    json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                    timeout=5).json()
                results['mcp_tools'] = [t['name'] for t in r['result']['tools']]
            except Exception as e:
                results['mcp_error'] = str(e)

        results['key'] = self.keys()
        try:
            results['catalog'] = self.models(limit=1)['total']
        except Exception as e:
            results['catalog'] = f'error: {e}'

        if not (self.api_key or self.key()):
            results['chat'] = f'skipped (no {ENV_KEY} / {KEY_FILE})'
        else:
            try:
                results['chat'] = self.forward('Say "ok" and nothing else.', max_tokens=10)
            except Exception as e:
                results['chat'] = f'error: {e}'

        return results
