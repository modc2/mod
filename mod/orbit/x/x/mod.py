"""x — the X (Twitter) API v2, backed by a Rust MCP server.

The backend is x-rs/ (axum), and it serves three faces of one tool layer:
an MCP server (JSON-RPC 2.0 over Streamable HTTP at /mcp, plus --stdio for
clients like Claude Code), a REST API where every tool has a route (described
at /openapi.json), and a browser app at / that drives all of it. Each X
capability is defined exactly once; this Python mod is a thin client over it.

Credentials never live in the repo: ~/.mod/x/credentials.json (0600), env, or
per-call override.
"""

import os
import json
import subprocess
import itertools
import requests

# Credential names, and the env vars the Rust backend reads them from.
ENV_NAMES = {
    'bearer_token': 'X_BEARER_TOKEN',
    'api_key': 'X_API_KEY',
    'api_secret': 'X_API_SECRET',
    'access_token': 'X_ACCESS_TOKEN',
    'access_token_secret': 'X_ACCESS_SECRET',
}
CRED_FIELDS = tuple(ENV_NAMES)


class Mod:
    description = """
    X (Twitter) API v2 - search, read and post.
    Rust backend (x-rs): search, get_post, user, timeline, mentions, followers,
    following, me, post, delete_post, like, repost, follow — exposed three ways
    off one tool layer: MCP (/mcp, stdio), REST (/openapi.json) and a browser
    app at /.
    """

    def __init__(self, bearer_token: str = None, server_url: str = None, **kwargs):
        self.dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.bearer_token = bearer_token or os.environ.get('X_BEARER_TOKEN', '') \
            or self.credentials().get('bearer_token', '')
        cfg = self._load_config()
        self.port = cfg.get('port', 50350)
        self.server_url = server_url or f'http://127.0.0.1:{self.port}'
        self._rpc_ids = itertools.count(1)

    # ── config / secrets ─────────────────────────────────────────

    def _load_config(self):
        cfg_path = os.path.join(self.dir, 'config.json')
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                return json.load(f)
        return {}

    @property
    def cred_path(self):
        return os.path.expanduser('~/.mod/x/credentials.json')

    def credentials(self):
        """Stored credentials (off-tree). Returns {} when none are set."""
        if os.path.exists(self.cred_path):
            with open(self.cred_path) as f:
                try:
                    return json.load(f)
                except json.JSONDecodeError:
                    return {}
        return {}

    def set_keys(self, persist: bool = False, **keys):
        """Set X credentials; persist=True writes ~/.mod/x/credentials.json (0600).

        Reads need `bearer_token`. Acting as an account (post/like/follow) needs
        the four OAuth 1.0a legs: api_key, api_secret, access_token,
        access_token_secret.
        """
        unknown = [k for k in keys if k not in CRED_FIELDS]
        if unknown:
            raise ValueError(f'unknown credential(s): {unknown}; expected {list(CRED_FIELDS)}')
        merged = {**self.credentials(), **{k: v for k, v in keys.items() if v}}
        if 'bearer_token' in keys:
            self.bearer_token = keys['bearer_token']
        if persist:
            os.makedirs(os.path.dirname(self.cred_path), exist_ok=True)
            with open(self.cred_path, 'w') as f:
                json.dump(merged, f, indent=2)
            os.chmod(self.cred_path, 0o600)
        else:
            # Not persisted → put them in this process's env, which `serve()`
            # hands to the backend. An already-running server won't see them.
            for k, v in keys.items():
                os.environ[ENV_NAMES[k]] = v
        return {'set': sorted(k for k in keys if k in CRED_FIELDS),
                'persisted': persist,
                'path': self.cred_path if persist else None}

    def auth_status(self):
        """Which credential rails are configured (never the secrets)."""
        return self.mcp_call('auth_status')

    # ── MCP client (the backend) ─────────────────────────────────

    def _server_up(self):
        try:
            return requests.get(f'{self.server_url}/health', timeout=2).ok
        except Exception:
            return False

    def mcp_call(self, tool: str, arguments: dict = None, **kwargs):
        """Call an MCP tool on the Rust backend (JSON-RPC tools/call)."""
        arguments = {k: v for k, v in dict(arguments or {}, **kwargs).items() if v is not None}
        headers = {'Content-Type': 'application/json'}
        if self.bearer_token:
            headers['x-api-key'] = self.bearer_token
        try:
            resp = requests.post(
                f'{self.server_url}/mcp',
                json={
                    'jsonrpc': '2.0',
                    'id': next(self._rpc_ids),
                    'method': 'tools/call',
                    'params': {'name': tool, 'arguments': arguments},
                },
                headers=headers, timeout=90,
            )
        except requests.ConnectionError:
            raise RuntimeError(
                f'x backend not reachable at {self.server_url} — start it with `m x/serve`')
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
        """The MCP tool registry served by the backend."""
        return requests.get(f'{self.server_url}/tools', timeout=10).json()['tools']

    def openapi(self):
        """The REST surface as an OpenAPI 3.1 document (generated from the tool schemas)."""
        return requests.get(f'{self.server_url}/openapi.json', timeout=10).json()

    def endpoints(self):
        """Every REST route and the MCP tool it projects."""
        return requests.get(f'{self.server_url}/health', timeout=10).json()['endpoints']

    def app(self):
        """Where the browser app lives, and what it can do at the current auth level."""
        auth = self.auth_status()
        return {
            'app': self.server_url + '/',
            'openapi': f'{self.server_url}/openapi.json',
            'mcp': f'{self.server_url}/mcp',
            'views': ['search', 'post', 'user', 'mentions', 'compose', 'api', 'auth'],
            'reads': auth['reads'],
            'writes': auth['writes'],
            'hint': ('open it and add keys under Auth' if not auth['reads']
                     else 'ready — search, profiles and timelines are live'),
        }

    def mcp_config(self):
        """MCP client config snippets (stdio + Streamable HTTP)."""
        return {
            'stdio': {'command': self.binary, 'args': ['--stdio']},
            'http': {'url': f'{self.server_url}/mcp'},
            'claude_code': f'claude mcp add x -- {self.binary} --stdio',
        }

    # ── api surface (one thin wrapper per MCP tool) ──────────────

    def forward(self, query: str = None, **kwargs):
        """Default entry — search recent posts (or report auth state if empty)."""
        if not query:
            return self.auth_status()
        return self.search(query, **kwargs)

    def search(self, query: str, max_results: int = 10, sort_order: str = None,
               start_time: str = None, end_time: str = None, next_token: str = None):
        """Search public posts from the last 7 days (full X query syntax)."""
        return self.mcp_call('search', query=query, max_results=max_results,
                             sort_order=sort_order, start_time=start_time,
                             end_time=end_time, next_token=next_token)

    def counts(self, query: str, granularity: str = 'day'):
        """Post volume matching a query over time."""
        return self.mcp_call('counts', query=query, granularity=granularity)

    def get_post(self, id: str):
        """One post by id or x.com URL (works keyless via the syndication CDN)."""
        return self.mcp_call('get_post', id=str(id))

    def user(self, username: str):
        """Account profile by @handle or numeric id."""
        return self.mcp_call('user', username=username)

    def timeline(self, username: str, max_results: int = 10, exclude: str = None):
        """Recent posts from an account."""
        return self.mcp_call('timeline', username=username,
                             max_results=max_results, exclude=exclude)

    def mentions(self, username: str = None, max_results: int = 10):
        """Posts mentioning an account (defaults to the authenticated one)."""
        return self.mcp_call('mentions', username=username, max_results=max_results)

    def followers(self, username: str, max_results: int = 20):
        return self.mcp_call('followers', username=username, max_results=max_results)

    def following(self, username: str, max_results: int = 20):
        return self.mcp_call('following', username=username, max_results=max_results)

    def me(self):
        """The authenticated account (needs user-context credentials)."""
        return self.mcp_call('me')

    def post(self, text: str, reply_to: str = None, quote_post_id: str = None,
             poll_options: list = None, poll_duration_minutes: int = None):
        """Publish a post as the authenticated account."""
        return self.mcp_call('post', text=text, reply_to=reply_to,
                             quote_post_id=quote_post_id, poll_options=poll_options,
                             poll_duration_minutes=poll_duration_minutes)

    def delete_post(self, id: str):
        return self.mcp_call('delete_post', id=str(id))

    def like(self, id: str):
        return self.mcp_call('like', id=str(id))

    def repost(self, id: str):
        return self.mcp_call('repost', id=str(id))

    def follow(self, username: str):
        return self.mcp_call('follow', username=username)

    # ── build / serve / kill ─────────────────────────────────────

    @property
    def binary(self):
        return os.path.join(self.dir, 'x-rs', 'target', 'release', 'x-api')

    def build(self, **kwargs):
        """Build the Rust MCP server (cargo build --release)."""
        rs_dir = os.path.join(self.dir, 'x-rs')
        result = subprocess.run(
            ['cargo', 'build', '--release'],
            cwd=rs_dir, capture_output=True, text=True,
            env={**os.environ,
                 'PATH': os.environ['PATH'] + ':' + os.path.expanduser('~/.cargo/bin')},
        )
        if result.returncode != 0:
            return {'status': 'build_failed', 'stderr': result.stderr[-3000:]}
        return {'status': 'built', 'binary': self.binary}

    def serve(self, port=None, **kwargs):
        """Run the Rust MCP server under pm2 as x-api."""
        port = port or self.port
        if not os.path.exists(self.binary):
            built = self.build()
            if built.get('status') != 'built':
                return built
        self.kill()
        env = {**os.environ, 'PORT': str(port)}
        if self.bearer_token:
            env['X_BEARER_TOKEN'] = self.bearer_token
        subprocess.run(['pm2', 'start', self.binary, '--name', 'x-api'],
                       cwd=self.dir, env=env, capture_output=True)
        return {
            'api': f'http://localhost:{port}',
            'mcp': f'http://localhost:{port}/mcp',
            'console': f'http://localhost:{port}/ (browser)',
            'processes': ['x-api'],
        }

    def kill(self, **kwargs):
        """Stop the x backend."""
        killed = []
        for name in ['x-api', 'x.api']:
            r = subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
            if r.returncode == 0:
                killed.append(name)
        return {'killed': killed}

    # ── test ─────────────────────────────────────────────────────

    def test(self, **kwargs):
        """Connectivity test: server health, MCP handshake, live X calls."""
        results = {'server_url': self.server_url, 'server_up': self._server_up()}
        if not results['server_up']:
            results['hint'] = 'run `m x/serve` first'
            return results

        try:
            r = requests.post(f'{self.server_url}/mcp',
                              json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                    'params': {'protocolVersion': '2025-06-18'}},
                              timeout=5).json()
            results['mcp_initialize'] = r.get('result', {}).get('serverInfo')
            results['mcp_tools'] = [t['name'] for t in self.tools()]
        except Exception as e:
            results['mcp_error'] = str(e)

        results['auth'] = self.auth_status()

        # Keyless path — proves the upstream reach without credentials.
        try:
            post = self.get_post('20')
            results['keyless_get_post'] = post.get('data', {}).get('text')
        except Exception as e:
            results['keyless_get_post_error'] = str(e)

        if results['auth'].get('reads'):
            try:
                results['search'] = len(self.search('hello', max_results=10).get('data', []))
            except Exception as e:
                results['search_error'] = str(e)
        else:
            results['search'] = 'skipped (no bearer token)'

        return results
