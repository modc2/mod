"""chutes — Chutes.ai serverless GPU inference, backed by a Rust MCP server.

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


class Mod:
    description = """
    Chutes.ai - Serverless GPU inference platform.
    Rust MCP server backend (chutes-rs): chat, image generation, chute
    management exposed as MCP tools over Streamable HTTP (/mcp) and stdio.
    """

    def __init__(self, api_key: str = None, default_model: str = None,
                 base_url: str = None, server_url: str = None, **kwargs):
        self.dir = os.path.dirname(os.path.abspath(__file__))
        self.api_key = api_key or os.environ.get('CHUTES_API_KEY', '') or self._key_file()
        self.default_model = default_model or os.environ.get(
            'CHUTES_DEFAULT_MODEL', 'unsloth/Llama-3.3-70B-Instruct')
        self.base_url = base_url or os.environ.get(
            'CHUTES_BASE_URL', 'https://api.chutes.ai')
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

    def _key_file(self):
        path = os.path.expanduser('~/.mod/chutes/api_key')
        if os.path.exists(path):
            with open(path) as f:
                return f.read().strip()
        return ''

    def set_api_key(self, api_key: str, persist: bool = False):
        """Set API key; persist=True writes ~/.mod/chutes/api_key (off-chain)."""
        self.api_key = api_key
        if persist:
            d = os.path.expanduser('~/.mod/chutes')
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, 'api_key')
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

    def mcp_call(self, tool: str, arguments: dict = None, **kwargs):
        """Call an MCP tool on the Rust backend (JSON-RPC tools/call)."""
        arguments = dict(arguments or {}, **kwargs)
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['x-api-key'] = self.api_key
        resp = requests.post(
            f'{self.server_url}/mcp',
            json={
                'jsonrpc': '2.0',
                'id': next(self._rpc_ids),
                'method': 'tools/call',
                'params': {'name': tool, 'arguments': arguments},
            },
            headers=headers, timeout=180,
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
            'stdio': {'command': binary, 'args': ['--stdio'],
                      'env': {'CHUTES_API_KEY': '<your key>'}},
            'http': {'url': f'{self.server_url}/mcp'},
            'claude_code': f'claude mcp add chutes -- {binary} --stdio',
        }

    # ── direct fallback (server down) ────────────────────────────

    @property
    def headers(self):
        return {'Authorization': f'Bearer {self.api_key}',
                'Content-Type': 'application/json'}

    def _direct(self, method, path, body=None, params=None):
        resp = requests.request(
            method, f'{self.base_url}{path}',
            json=body, params=params, headers=self.headers, timeout=180)
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
        """Default entry — simple chat completion (or list chutes if empty)."""
        if not message:
            return self.list_chutes()
        result = self.chat(
            [{'role': 'user', 'content': message}],
            model=model, system=system_prompt or kwargs.pop('system', None),
            **kwargs)
        try:
            return result['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError):
            return result

    def chat(self, messages, model=None, system=None,
             temperature: float = 0.7, max_tokens: int = 4096, **kwargs):
        """OpenAI-compatible chat completion via the MCP `chat` tool."""
        if isinstance(messages, str):
            messages = [{'role': 'user', 'content': messages}]
        args = {'messages': messages, 'model': model or self.default_model,
                'temperature': temperature, 'max_tokens': max_tokens}
        if system:
            args['system'] = system

        def direct():
            body = {'model': args['model'], 'messages': messages,
                    'temperature': temperature, 'max_tokens': max_tokens,
                    'stream': False}
            if system:
                body['messages'] = [{'role': 'system', 'content': system}] + messages
            return self._direct('POST', '/v1/chat/completions', body)

        return self._call('chat', direct, **args)

    def generate_image(self, prompt: str, model: str = None,
                       size: str = '1024x1024', n: int = 1, **kwargs):
        """Generate images via the MCP `generate_image` tool."""
        args = {'prompt': prompt, 'size': size, 'n': n}
        if model:
            args['model'] = model

        def direct():
            body = dict(args, response_format='url')
            return self._direct('POST', '/v1/images/generations', body)

        return self._call('generate_image', direct, **args)

    def models(self, search: str = None, limit: int = 200):
        """List available models/chutes, optionally filtered."""
        args = {'limit': limit}
        if search:
            args['search'] = search

        def direct():
            data = self._direct('GET', '/chutes/', params={'page': 1, 'limit': limit})
            items = data.get('items', data if isinstance(data, list) else [])
            if search:
                q = search.lower()
                items = [c for c in items if q in json.dumps(c).lower()]
            return {'items': items, 'total': len(items)}

        return self._call('models', direct, **args)

    def list_chutes(self, page: int = 1, limit: int = 50):
        return self._call(
            'list_chutes',
            lambda: self._direct('GET', '/chutes/', params={'page': page, 'limit': limit}),
            page=page, limit=limit)

    def get_chute(self, chute_id: str):
        return self._call(
            'get_chute',
            lambda: self._direct('GET', f'/chutes/{chute_id}'),
            chute_id=chute_id)

    def deploy_chute(self, config: dict):
        return self._call(
            'deploy_chute',
            lambda: self._direct('POST', '/chutes/', config),
            config=config)

    def delete_chute(self, chute_id: str):
        return self._call(
            'delete_chute',
            lambda: self._direct('DELETE', f'/chutes/{chute_id}'),
            chute_id=chute_id)

    def warmup(self, chute_id: str):
        return self._call(
            'warmup',
            lambda: self._direct('GET', f'/chutes/warmup/{chute_id}'),
            chute_id=chute_id)

    def utilization(self):
        return self._call(
            'utilization',
            lambda: self._direct('GET', '/chutes/utilization'))

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
        if self.api_key:
            env['CHUTES_API_KEY'] = self.api_key
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
        """Connectivity test: server health, MCP handshake, live upstream call."""
        results = {'server_url': self.server_url}
        results['server_up'] = self._server_up()

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

        try:
            util = self.utilization()
            results['upstream_connected'] = True
            if isinstance(util, list):
                results['upstream_chutes_reporting'] = len(util)
        except Exception as e:
            results['upstream_connected'] = False
            results['upstream_error'] = str(e)

        if results.get('upstream_connected') and self.api_key:
            try:
                results['chat'] = self.forward('Say "ok" and nothing else.', max_tokens=10)
            except Exception as e:
                results['chat_error'] = str(e)
        elif not self.api_key:
            results['chat'] = 'skipped (no CHUTES_API_KEY / ~/.mod/chutes/api_key)'

        return results
