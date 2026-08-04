"""targon — Bittensor subnet 4 GPU compute, backed by a Rust MCP server.

The backend is targon-rs/ (axum): an MCP server (JSON-RPC 2.0 over Streamable
HTTP at /mcp, plus --stdio for MCP clients like Claude Code) wrapping the
Targon Hub API — rent GPUs, deploy container/VM workloads, manage volumes,
SSH keys and templates. This Python mod is a thin client over that server,
with a direct-to-Targon fallback for the calls it exposes when the server
isn't running.

Key (off-tree): TARGON_API_KEY or ~/.mod/targon/api_key. Inventory, version
and health need no key.
"""

import os
import json
import subprocess
import itertools
import requests


class Mod:
    description = """
    Targon (Bittensor SN4) - decentralized GPU compute.
    Rust MCP server backend (targon-rs): inventory, rentals/VMs, volumes,
    SSH keys, templates and account exposed as MCP tools over Streamable
    HTTP (/mcp) and stdio.
    """

    # Tool name -> (method, path), for the tools this mod exposes. Used only
    # when the MCP server is down; the server's table is the full surface.
    ROUTES = {
        'inventory':        ('GET', '/tha/v2/inventory'),
        'version':          ('GET', '/tha/v2/version'),
        'list_workloads':   ('GET', '/tha/v2/workloads'),
        'get_workload':     ('GET', '/tha/v2/workloads/{workload_uid}'),
        'deploy_workload':  ('POST', '/tha/v2/workloads/{workload_uid}/deploy'),
        'suspend_workload': ('POST', '/tha/v2/workloads/{workload_uid}/suspend'),
        'delete_workload':  ('DELETE', '/tha/v2/workloads/{workload_uid}'),
        'workload_state':   ('GET', '/tha/v2/workloads/{workload_uid}/state'),
        'workload_logs':    ('GET', '/tha/v2/workloads/{workload_uid}/logs'),
        'workload_events':  ('GET', '/tha/v2/workloads/{workload_uid}/events'),
        'list_volumes':     ('GET', '/tha/v2/volumes'),
        'create_volume':    ('POST', '/tha/v2/volumes'),
        'list_ssh_keys':    ('GET', '/tha/v2/ssh-keys'),
        'create_ssh_key':   ('POST', '/tha/v2/ssh-keys'),
        'list_templates':   ('GET', '/tha/v2/templates'),
        'credits':          ('GET', '/tha/v2/me/credits'),
        'wallet':           ('GET', '/tha/v2/me/wallet'),
    }

    def __init__(self, api_key: str = None, base_url: str = None,
                 server_url: str = None, **kwargs):
        self.dir = os.path.dirname(os.path.abspath(__file__))
        self.api_key = api_key or os.environ.get('TARGON_API_KEY', '') or self._key_file()
        self.base_url = base_url or os.environ.get('TARGON_BASE_URL', 'https://api.targon.com')
        cfg = self._load_config()
        self.port = cfg.get('port', 50440)
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
        path = os.path.expanduser('~/.mod/targon/api_key')
        if os.path.exists(path):
            with open(path) as f:
                return f.read().strip()
        return ''

    def set_api_key(self, api_key: str, persist: bool = True):
        """Set the Targon API key; persist=True writes ~/.mod/targon/api_key."""
        self.api_key = api_key
        if persist:
            d = os.path.expanduser('~/.mod/targon')
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, 'api_key')
            with open(path, 'w') as f:
                f.write(api_key)
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

    def tools(self):
        """The MCP tools the backend exposes (name + description)."""
        tools = requests.get(f'{self.server_url}/tools', timeout=10).json()['tools']
        return [{'name': t['name'], 'description': t['description']} for t in tools]

    def mcp_config(self):
        """MCP client config snippets (stdio + Streamable HTTP)."""
        return {
            'stdio': {'command': self.binary, 'args': ['--stdio'],
                      'env': {'TARGON_API_KEY': '<your key>'}},
            'http': {'url': f'{self.server_url}/mcp'},
            'claude_code': f'claude mcp add targon -- {self.binary} --stdio',
        }

    # ── direct fallback (server down) ────────────────────────────

    def _direct(self, tool, args):
        method, path = self.ROUTES[tool]
        args = dict(args)
        for key in list(args):
            token = '{%s}' % key
            if token in path:
                path = path.replace(token, str(args.pop(key)))
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        resp = requests.request(
            method, f'{self.base_url}{path}', headers=headers, timeout=180,
            params=args if method == 'GET' else None,
            json=args if method != 'GET' and args else None,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f'upstream {resp.status_code}: {resp.text}')
        try:
            return resp.json()
        except ValueError:
            return {'output': resp.text} if resp.text else {'ok': True}

    def _call(self, tool, **args):
        """Route through the MCP server; fall back to api.targon.com directly."""
        args = {k: v for k, v in args.items() if v is not None}
        if self._server_up():
            return self.mcp_call(tool, args)
        if tool not in self.ROUTES:
            raise RuntimeError(f'{tool} needs the MCP server — run `m targon/serve`')
        return self._direct(tool, args)

    # ── api surface ──────────────────────────────────────────────

    def forward(self, action: str = None, **kwargs):
        """Default entry — any MCP tool by name, or the inventory when empty."""
        if not action:
            return self.inventory(**kwargs)
        return self._call(action, **kwargs)

    def inventory(self, type: str = None, gpu: bool = None):
        """Compute tiers with hourly price and live availability (no key needed)."""
        return self._call('inventory', type=type, gpu=gpu)

    def cheapest(self, gpu: bool = True, gpu_type: str = None, min_gpus: int = None,
                 max_cost_per_hour: float = None, type: str = None):
        """Cheapest available tier matching a filter (needs the MCP server)."""
        return self._call('cheapest', gpu=gpu, gpu_type=gpu_type, min_gpus=min_gpus,
                          max_cost_per_hour=max_cost_per_hour, type=type)

    def rent(self, name: str, image: str = None, resource_name: str = None,
             gpu_type: str = None, ssh_keys: list = None, ports: list = None,
             envs: list = None, deploy: bool = True, **kwargs):
        """Register + deploy a machine in one call. Omit resource_name to auto-pick."""
        return self._call('rent', name=name, image=image, resource_name=resource_name,
                          gpu_type=gpu_type, ssh_keys=ssh_keys, ports=ports, envs=envs,
                          deploy=deploy, **kwargs)

    def workloads(self, limit: int = None, status: str = None, type: str = None):
        """List your workloads."""
        return self._call('list_workloads', limit=limit, status=status, type=type)

    def workload(self, workload_uid: str):
        """Full record for one workload."""
        return self._call('get_workload', workload_uid=workload_uid)

    def deploy(self, workload_uid: str):
        """Provision a registered workload."""
        return self._call('deploy_workload', workload_uid=workload_uid)

    def suspend(self, workload_uid: str):
        """Suspend a rental and release its runtime (config kept)."""
        return self._call('suspend_workload', workload_uid=workload_uid)

    def delete_workload(self, workload_uid: str):
        """Tear down a workload; billing stops."""
        return self._call('delete_workload', workload_uid=workload_uid)

    def state(self, workload_uid: str):
        """Status, access URLs, public IP and SSH port."""
        return self._call('workload_state', workload_uid=workload_uid)

    def logs(self, workload_uid: str, tail: int = 200, since: str = None):
        """Container (or VM) logs."""
        return self._call('workload_logs', workload_uid=workload_uid, tail=tail, since=since)

    def events(self, workload_uid: str, limit: int = None):
        """Lifecycle events — where failed deploys explain themselves."""
        return self._call('workload_events', workload_uid=workload_uid, limit=limit)

    def exec(self, workload_uid: str, command):
        """Run a command inside a rental and return its output."""
        return self._call('workload_exec', workload_uid=workload_uid, command=command)

    def volumes(self, limit: int = None):
        """List your persistent volumes."""
        return self._call('list_volumes', limit=limit)

    def create_volume(self, name: str, size_in_mb: int, resource_name: str):
        """Create a persistent volume."""
        return self._call('create_volume', name=name, size_in_mb=size_in_mb,
                          resource_name=resource_name)

    def ssh_keys(self):
        """List registered SSH keys."""
        return self._call('list_ssh_keys')

    def create_ssh_key(self, name: str, ssh_key: str):
        """Register a public SSH key for workload access."""
        return self._call('create_ssh_key', name=name, ssh_key=ssh_key)

    def templates(self, visibility: str = None):
        """List workload templates."""
        return self._call('list_templates', visibility=visibility)

    def credits(self):
        """Your credit balance."""
        return self._call('credits')

    def wallet(self):
        """Your Bittensor SS58 wallet address on Targon."""
        return self._call('wallet')

    def version(self):
        """Targon Hub API version (no key needed)."""
        return self._call('version')

    # ── build / serve / kill ─────────────────────────────────────

    @property
    def binary(self):
        return os.path.join(self.dir, 'targon-rs', 'target', 'release', 'targon-api')

    def build(self, **kwargs):
        """Build the Rust MCP server (cargo build --release)."""
        result = subprocess.run(
            ['cargo', 'build', '--release'],
            cwd=os.path.join(self.dir, 'targon-rs'), capture_output=True, text=True,
            env={**os.environ, 'PATH': os.environ['PATH'] + ':' + os.path.expanduser('~/.cargo/bin')},
        )
        if result.returncode != 0:
            return {'status': 'build_failed', 'stderr': result.stderr[-3000:]}
        return {'status': 'built', 'binary': self.binary}

    def serve(self, port=None, **kwargs):
        """Run the Rust MCP server under pm2 as targon-api."""
        port = port or self.port
        if not os.path.exists(self.binary):
            built = self.build()
            if built.get('status') != 'built':
                return built
        self.kill()
        env = {**os.environ, 'PORT': str(port)}
        if self.api_key:
            env['TARGON_API_KEY'] = self.api_key
        subprocess.run(['pm2', 'start', self.binary, '--name', 'targon-api'],
                       cwd=self.dir, env=env, capture_output=True)
        return {
            'api': f'http://localhost:{port}',
            'mcp': f'http://localhost:{port}/mcp',
            'console': f'http://localhost:{port}/ (browser)',
            'processes': ['targon-api'],
        }

    def kill(self, **kwargs):
        """Stop the targon backend."""
        killed = []
        for name in ['targon-api', 'targon.api']:
            r = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    # ── test ─────────────────────────────────────────────────────

    def test(self, **kwargs):
        """Connectivity test: server health, MCP handshake, live upstream calls."""
        results = {'server_url': self.server_url, 'server_up': self._server_up()}

        if results['server_up']:
            try:
                r = requests.post(
                    f'{self.server_url}/mcp',
                    json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                          'params': {'protocolVersion': '2025-06-18'}}, timeout=5).json()
                results['mcp_initialize'] = r.get('result', {}).get('serverInfo')
                results['mcp_tools'] = len(self.tools())
                results['cheapest_gpu'] = self.cheapest().get('resource_name')
            except Exception as e:
                results['mcp_error'] = str(e)

        try:
            results['upstream_version'] = self.version()
            tiers = self.inventory()
            results['tiers'] = len(tiers)
            results['available_now'] = sum(1 for t in tiers if t.get('available', 0) > 0)
        except Exception as e:
            results['upstream_error'] = str(e)

        if self.api_key:
            try:
                results['credits'] = self.credits()
            except Exception as e:
                results['credits_error'] = str(e)
        else:
            results['credits'] = 'skipped (no TARGON_API_KEY / ~/.mod/targon/api_key)'

        return results
