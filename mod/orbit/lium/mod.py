"""lium — rent GPUs from Bittensor subnet 51, backed by a Rust MCP server.

The backend is lium-rs/ (axum): an MCP server (JSON-RPC 2.0 over Streamable
HTTP at /mcp, plus --stdio for MCP clients like Claude Code) that speaks the
Lium platform API (https://lium.io/api) upstream. Every REST route and every
fn here dispatches through the same MCP tool layer, so there is one definition
of each capability — and this Python mod falls back to calling lium.io
directly when the server is not running.

Auth is the caller's own key: x-api-key header > LIUM_API_KEY > ~/.mod/lium/api_key.
Public reads (nodes, templates, stats, subnet weights) need no key at all.
"""

import os
import json
import itertools
import subprocess

import requests

USER_AGENT = 'lium-mod/0.1 (mod protocol)'


class Mod:
    description = """
    Lium — the GPU rental marketplace of Bittensor subnet 51.
    Rust MCP server backend (lium-rs): browse nodes, rent pods, read subnet
    state and validator weights, exposed as MCP tools over Streamable HTTP
    (/mcp) and stdio.
    """

    def __init__(self, api_key: str = None, base_url: str = None,
                 server_url: str = None, **kwargs):
        self.dir = os.path.dirname(os.path.abspath(__file__))
        self.api_key = api_key or os.environ.get('LIUM_API_KEY', '') or self._key_file()
        self.base_url = base_url or os.environ.get('LIUM_BASE_URL', 'https://lium.io/api')
        cfg = self._load_config()
        self.port = cfg.get('port', 50430)
        self.netuid = cfg.get('netuid', 51)
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
        path = os.path.expanduser('~/.mod/lium/api_key')
        if os.path.exists(path):
            with open(path) as f:
                return f.read().strip()
        return ''

    def set_api_key(self, api_key: str, persist: bool = False):
        """Set the Lium API key; persist=True writes ~/.mod/lium/api_key (off-tree, 0600)."""
        self.api_key = api_key.strip()
        if persist:
            d = os.path.expanduser('~/.mod/lium')
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, 'api_key')
            with open(path, 'w') as f:
                f.write(self.api_key)
            os.chmod(path, 0o600)
        return {'status': 'set', 'persisted': persist}

    # ── MCP client (the backend) ─────────────────────────────────

    def _server_up(self):
        try:
            return requests.get(f'{self.server_url}/health', timeout=2).ok
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
        return {
            'stdio': {'command': self.binary, 'args': ['--stdio'],
                      'env': {'LIUM_API_KEY': '<your key>'}},
            'http': {'url': f'{self.server_url}/mcp'},
            'claude_code': f'claude mcp add lium -- {self.binary} --stdio',
        }

    def tools(self):
        """The MCP tool registry served by the backend."""
        if self._server_up():
            return requests.get(f'{self.server_url}/tools', timeout=10).json()
        return {'error': 'backend down — m lium/serve'}

    # ── direct fallback (server down) ────────────────────────────

    @property
    def headers(self):
        h = {'Content-Type': 'application/json', 'User-Agent': USER_AGENT}
        if self.api_key:
            h['X-API-Key'] = self.api_key
        return h

    def _direct(self, method, path, body=None, params=None):
        resp = requests.request(method, f'{self.base_url}{path}', json=body,
                                params=params, headers=self.headers, timeout=120)
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def _call(self, tool, direct, **arguments):
        """Route through the MCP server; fall back to lium.io directly."""
        if self._server_up():
            return self.mcp_call(tool, arguments)
        return direct()

    # ── marketplace ──────────────────────────────────────────────

    def forward(self, **kwargs):
        """Default entry — server and upstream status."""
        return self.info()

    def info(self):
        """Server + upstream status."""
        return self._call('lium_info', lambda: {
            'name': 'lium', 'netuid': self.netuid, 'upstream': self.base_url,
            'backend': 'down (direct mode)', 'key_loaded': bool(self.api_key),
        })

    def executors(self, gpu_type: str = None, max_price: float = None,
                  min_gpus: int = None, country: str = None, tier: str = None,
                  available_only: bool = False, sort: str = 'price',
                  limit: int = 50, raw: bool = False):
        """Browse the SN51 GPU marketplace (public — no key needed)."""
        args = {k: v for k, v in dict(
            gpu_type=gpu_type, max_price=max_price, min_gpus=min_gpus,
            country=country, tier=tier, available_only=available_only,
            sort=sort, limit=limit, raw=raw).items() if v not in (None, False)}
        return self._call('executors', lambda: self._direct(
            'GET', '/executors', params={'size': 500}), **args)

    # `ls` is what the Lium CLI calls this; keep the muscle memory.
    ls = executors

    def executor(self, executor_id: str):
        """One node by id or unique prefix."""
        return self._call(
            'executor',
            lambda: next((e for e in self._direct('GET', '/executors', params={'size': 500})
                          if str(e.get('id', '')).startswith(executor_id)), None),
            executor_id=executor_id)

    def gpu_types(self):
        """Rented vs total nodes per GPU type."""
        return self._call('gpu_types', lambda: {'stats': self._direct('GET', '/executors/stats')})

    def capacity(self):
        """Open earning capacity per GPU model."""
        return self._call('capacity', lambda: {'capacity': self._direct('GET', '/machines/capacity')})

    def subnet(self):
        """Subnet 51 in one call: supply, utilization, capacity, validator weights."""
        return self._call('subnet', lambda: {
            'netuid': self.netuid,
            'utilization_by_gpu': self._direct('GET', '/executors/stats'),
            'capacity': self._direct('GET', '/machines/capacity'),
            'weights': self._direct('GET', '/latest-set-weights'),
        })

    def provider(self, miner_hotkey: str, executors: bool = False):
        """Provider (miner) statistics by Bittensor hotkey."""
        return self._call(
            'provider',
            lambda: {'stats': self._direct('GET', f'/provider-stats/{miner_hotkey}')},
            miner_hotkey=miner_hotkey, executors=executors)

    def templates(self, q: str = None, gpu_model: str = None,
                  driver_version: str = None, limit: int = 50):
        """Docker templates you can launch on a pod."""
        args = {k: v for k, v in dict(q=q, gpu_model=gpu_model,
                                      driver_version=driver_version, limit=limit).items() if v}
        return self._call('templates', lambda: {'templates': self._direct('GET', '/templates')}, **args)

    # ── pods ─────────────────────────────────────────────────────

    def pods(self, raw: bool = False):
        """Your running pods (needs a key)."""
        return self._call('pods', lambda: {'pods': self._direct('GET', '/pods')}, raw=raw)

    ps = pods

    def pod(self, pod_id: str):
        """One pod, with ssh command and port mappings."""
        return self._call('pod', lambda: self._direct('GET', f'/pods/{pod_id}'), pod_id=pod_id)

    def up(self, executor_id: str, name: str = 'mod-pod', template_id: str = None,
           gpu_count: int = None, public_key: str = None, termination_hours: int = None,
           enable_jupyter: bool = None, volume_id: str = None):
        """Rent a node — start a pod. Spends credits on the account behind the key."""
        args = {k: v for k, v in dict(
            executor_id=executor_id, name=name, template_id=template_id,
            gpu_count=gpu_count, public_key=public_key,
            termination_hours=termination_hours, enable_jupyter=enable_jupyter,
            volume_id=volume_id).items() if v is not None}
        # Renting picks a template and your ssh keys first; that resolution
        # lives in the MCP server, so there is no direct fallback for it.
        if not self._server_up():
            raise RuntimeError('renting goes through the MCP server — m lium/serve')
        return self.mcp_call('up', args)

    def down(self, pod_id: str):
        """Stop a pod and end the rental."""
        return self._call('down', lambda: self._direct('DELETE', f'/pods/{pod_id}'), pod_id=pod_id)

    rm = down

    def reboot(self, pod_id: str, volume_id: str = None):
        """Reboot a pod."""
        args = {'pod_id': pod_id}
        if volume_id:
            args['volume_id'] = volume_id
        return self._call('reboot', lambda: self._direct('POST', f'/pods/{pod_id}/reboot', {}), **args)

    def logs(self, pod_id: str, tail: int = 200):
        """Container logs for a pod."""
        return self._call(
            'logs',
            lambda: self._direct('GET', f'/pods/{pod_id}/logs', params={'tail': tail, 'follow': False}),
            pod_id=pod_id, tail=tail)

    # ── account ──────────────────────────────────────────────────

    def me(self):
        """The account behind the key: identity and credit balance."""
        return self._call('me', lambda: self._direct('GET', '/users/me'))

    def ssh_keys(self):
        """SSH keys registered on the account."""
        return self._call('ssh_keys', lambda: {'ssh_keys': self._direct('GET', '/ssh-keys')})

    def add_ssh_key(self, public_key: str, name: str = 'mod-lium'):
        """Register an SSH public key on the account."""
        return self._call(
            'add_ssh_key',
            lambda: self._direct('POST', '/ssh-keys', {'name': name, 'public_key': public_key}),
            public_key=public_key, name=name)

    def volumes(self):
        """Persistent volumes on the account."""
        return self._call('volumes', lambda: {'volumes': self._direct('GET', '/volumes')})

    # ── api explorer ─────────────────────────────────────────────

    def endpoints(self, q: str = None):
        """Every operation on the live Lium API, from its published OpenAPI spec."""
        args = {'q': q} if q else {}
        return self._call('endpoints', lambda: self._direct('GET', '/openapi.json'), **args)

    def api(self, path: str, method: str = 'GET', query: dict = None, body: dict = None):
        """Call any Lium API endpoint directly — the escape hatch."""
        args = {'path': path, 'method': method}
        if query:
            args['query'] = query
        if body:
            args['body'] = body
        return self._call('api', lambda: self._direct(method, path, body, query), **args)

    # ── build / serve / kill ─────────────────────────────────────

    @property
    def binary(self):
        return os.path.join(self.dir, 'lium-rs', 'target', 'release', 'lium-api')

    def build(self, **kwargs):
        """Build the Rust MCP server (cargo build --release)."""
        result = subprocess.run(
            ['cargo', 'build', '--release'],
            cwd=os.path.join(self.dir, 'lium-rs'), capture_output=True, text=True,
            env={**os.environ, 'PATH': os.environ['PATH'] + ':' + os.path.expanduser('~/.cargo/bin')},
        )
        if result.returncode != 0:
            return {'status': 'build_failed', 'stderr': result.stderr[-3000:]}
        return {'status': 'built', 'binary': self.binary}

    def serve(self, port=None, **kwargs):
        """Run the Rust MCP server under pm2 as lium-api (API + console, one port)."""
        port = port or self.port
        if not os.path.exists(self.binary):
            built = self.build()
            if built.get('status') != 'built':
                return built
        self.kill()
        env = {**os.environ, 'PORT': str(port)}
        if self.api_key:
            env['LIUM_API_KEY'] = self.api_key
        subprocess.run(['pm2', 'start', self.binary, '--name', 'lium-api'],
                       cwd=self.dir, env=env, capture_output=True)
        return {
            'api': f'http://localhost:{port}',
            'mcp': f'http://localhost:{port}/mcp',
            'console': f'http://localhost:{port}/lium (browser)',
            'processes': ['lium-api'],
        }

    def kill(self, **kwargs):
        """Stop the lium backend."""
        killed = []
        for name in ['lium-api', 'lium.api', 'lium-app']:
            r = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    # ── test ─────────────────────────────────────────────────────

    def test(self, **kwargs):
        """Connectivity test: server health, MCP handshake, live upstream reads."""
        results = {'server_url': self.server_url, 'server_up': self._server_up()}

        if results['server_up']:
            try:
                r = requests.post(f'{self.server_url}/mcp', timeout=10, json={
                    'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                    'params': {'protocolVersion': '2025-06-18'}}).json()
                results['mcp_initialize'] = r.get('result', {}).get('serverInfo')
                r = requests.post(f'{self.server_url}/mcp', timeout=10, json={
                    'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}).json()
                results['mcp_tools'] = [t['name'] for t in r['result']['tools']]
            except Exception as e:
                results['mcp_error'] = str(e)

        try:
            market = self.executors(limit=5)
            results['upstream_connected'] = True
            results['nodes_listed'] = market.get('listed', len(market.get('executors', [])))
            results['cheapest_gpu_hr'] = min(
                (e['price_per_gpu_hr'] for e in market.get('executors', [])), default=None)
        except Exception as e:
            results['upstream_connected'] = False
            results['upstream_error'] = str(e)

        try:
            results['subnet'] = self.subnet().get('marketplace')
        except Exception as e:
            results['subnet_error'] = str(e)

        if self.api_key:
            try:
                results['pods'] = self.pods().get('count')
            except Exception as e:
                results['pods_error'] = str(e)
        else:
            results['pods'] = 'skipped (no LIUM_API_KEY / ~/.mod/lium/api_key)'

        return results

    def readme(self):
        """Return the project README."""
        path = os.path.join(self.dir, 'README.md')
        if os.path.exists(path):
            with open(path) as f:
                return f.read()
        return None
