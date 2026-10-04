"""
mcp - every fleet module's MCP server, as tools

The fleet already reaches the agent two ways: `mod.<name>` calls a module's
forward() in-process with one generic fn/params convention. This registry is
the richer door: ~50 fleet modules serve Model Context Protocol (Streamable
HTTP, JSON-RPC 2.0) off their own port, and each MCP tool comes with a real
JSON schema — name, description, typed arguments. Mounted here they become
`mcp.<mod>.<tool>` (`mcp.build.list_modules`, `mcp.dns.dns_resolve`, …) and
the model sees proper per-tool signatures instead of guessing kwargs.

Local-first by design: discovery is a scan of the fleet's own config.json
files (urls.mcp / endpoints.mcp / an `mcp` block), candidates are probed with
a real initialize + tools/list handshake, and whichever URL answers wins.
Nothing here talks to a registry off this box; the orbit/mcp hub, when it
runs, is just one more discovered server (and through it, its whole union).

Same rules as the fleet registry next door:
  - *potential* tools — off the default loadout, snapped on by name or
    toolbox (see tools/mod.py), so 600 tool schemas never drown a prompt
  - host-only in a sandboxed run (run_plan blocks any non-builtin kind)

State lives OFF-tree in ~/.mod/agent/mcp/: index.json (the probed catalog,
stale kept on error) and auth.json (optional per-server headers — secrets
never sit in the module dir).

Usage:
    mcp = McpTools()
    mcp.ls()                                   # ['mcp.build.build_info', …]
    mcp.servers()                              # one row per fleet MCP server
    mcp.run('mcp.dns.dns_resolve', name='agent')
    mcp.schema(['mcp.build.list_modules'])     # LLM view, real arg types
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

PREFIX = 'mcp.'
PROTOCOL_VERSION = '2025-06-18'
CLIENT_INFO = {'name': 'orbit-agent', 'version': '1.0'}

# a probe is ~50 local handshakes in parallel — cheap enough to redo hourly,
# too slow (and too wake-happy) to redo per call
INDEX_TTL = int(os.environ.get('AGENT_MCP_TTL', 3600))
RETRY_COOLDOWN = 60          # a server that just failed isn't re-probed per call
PROBE_TIMEOUT = float(os.environ.get('AGENT_MCP_TIMEOUT', 4))
CALL_TIMEOUT = 90
MAX_TOOLS = 200              # per server; a hub re-exporting the world stays sane
MAX_DESC = 220
MAX_HINT = 300
MAX_OUTPUT = 20_000
PROBE_WORKERS = 16
# calling our own MCP server would start a second agent loop inside the one
# already running — the `task` tool is the supported way to delegate
SKIP = ('agent',)
# a scaled-to-zero mod refuses its own port; the activator's API route wakes
# it and proxies the call (never used by background probes, so scale-to-zero
# still works — only an actual tool call is worth waking a module for)
ACTIVATOR = 'http://127.0.0.1:9000/api/{mod}/mcp'

STATE_DIR = Path(os.path.expanduser('~/.mod/agent/mcp'))


def _clip(text: str, limit: int = MAX_DESC) -> str:
    text = ' '.join((text or '').split())
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)[:MAX_OUTPUT]


def _parse_body(raw: bytes, content_type: str) -> Optional[Dict]:
    """A Streamable HTTP reply is plain JSON or an SSE stream of `data:`
    lines — take the last data event that parses as a JSON-RPC message."""
    text = raw.decode('utf-8', 'replace').strip()
    if not text:
        return None
    if 'text/event-stream' in (content_type or '') or text.startswith(('event:', 'data:', ':')):
        message = None
        for line in text.splitlines():
            if line.startswith('data:'):
                try:
                    parsed = json.loads(line[5:].strip())
                except Exception:
                    continue
                if isinstance(parsed, dict) and ('result' in parsed or 'error' in parsed):
                    message = parsed
        return message
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


class McpError(Exception):
    pass


class Client:
    """One MCP server over Streamable HTTP. Handshakes lazily, keeps the
    session id, re-handshakes once when the server forgot it."""

    def __init__(self, url: str, headers: Dict[str, str] = None,
                 timeout: float = CALL_TIMEOUT):
        self.url = url
        self.headers = dict(headers or {})
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self._id = 0

    def _post(self, payload: Dict, timeout: float) -> (Optional[Dict], Dict):
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
            'MCP-Protocol-Version': PROTOCOL_VERSION,
            **self.headers,
        }
        if self.session_id:
            headers['Mcp-Session-Id'] = self.session_id
        req = urllib.request.Request(self.url, json.dumps(payload).encode(),
                                     headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                sid = resp.headers.get('Mcp-Session-Id')
                if sid:
                    self.session_id = sid
                body = resp.read()
                ctype = resp.headers.get('Content-Type', '')
        except urllib.error.HTTPError as e:
            # a 4xx with a body is often a JSON-RPC error worth reading;
            # 404/400 on a stale session is the re-handshake signal
            body, ctype = e.read(), e.headers.get('Content-Type', '')
            parsed = _parse_body(body, ctype)
            if parsed is None:
                raise McpError(f"HTTP {e.code} from {self.url}")
            return parsed, {'status': e.code}
        return _parse_body(body, ctype), {'status': 200}

    def rpc(self, method: str, params: Dict = None,
            timeout: float = None) -> Dict:
        self._id += 1
        payload = {'jsonrpc': '2.0', 'id': self._id, 'method': method,
                   'params': params or {}}
        message, meta = self._post(payload, timeout or self.timeout)
        if message is None:
            raise McpError(f"{method}: empty or unparseable reply from {self.url}")
        if 'error' in message:
            err = message['error'] or {}
            raise McpError(f"{method}: {err.get('message', err)} "
                           f"(code {err.get('code')})")
        return message.get('result') or {}

    def notify(self, method: str, params: Dict = None):
        payload = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
        try:
            self._post(payload, PROBE_TIMEOUT)
        except Exception:
            pass   # a notification is best-effort by definition

    def handshake(self, timeout: float = None) -> Dict:
        result = self.rpc('initialize', {
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {},
            'clientInfo': CLIENT_INFO,
        }, timeout=timeout or PROBE_TIMEOUT)
        self.notify('notifications/initialized')
        return result

    def tools(self, timeout: float = None) -> List[Dict]:
        """tools/list, following the cursor a few pages deep."""
        out, cursor = [], None
        for _ in range(5):
            params = {'cursor': cursor} if cursor else {}
            result = self.rpc('tools/list', params,
                              timeout=timeout or PROBE_TIMEOUT)
            out += [t for t in result.get('tools') or [] if isinstance(t, dict)]
            cursor = result.get('nextCursor')
            if not cursor or len(out) >= MAX_TOOLS:
                break
        return out[:MAX_TOOLS]

    def call(self, name: str, arguments: Dict = None) -> Dict:
        try:
            return self.rpc('tools/call',
                            {'name': name, 'arguments': arguments or {}})
        except McpError:
            # the one retry: a restarted server dropped our session
            self.session_id = None
            self.handshake(timeout=self.timeout)
            return self.rpc('tools/call',
                            {'name': name, 'arguments': arguments or {}})


class McpTools:
    """Every fleet MCP server as a tool registry — `mcp.<mod>.<tool>`."""
    description = ("MCP registry - every fleet module that serves Model "
                   "Context Protocol, each of its tools callable by name")

    def __init__(self, ttl: int = INDEX_TTL, roots: List[str] = None,
                 state_dir: str = None, **kwargs):
        self._ttl = ttl
        self._roots = [Path(r) for r in roots] if roots else self._fleet_roots()
        self._dir = Path(state_dir) if state_dir else STATE_DIR
        self._lock = threading.Lock()
        self._index: Dict[str, Dict] = {}
        self._read = 0.0
        self._last_attempt = 0.0
        self._clients: Dict[str, Client] = {}
        self._load()

    # ── discovery (config scan, no network) ─────────────────────────

    @staticmethod
    def _fleet_roots() -> List[Path]:
        """The directories modules live in, derived from where we are:
        …/mod/orbit/agent/src/tools/mcp/mod.py → …/mod/{core,orbit}."""
        base = Path(__file__).resolve().parents[4].parent   # …/mod
        return [base / 'core', base / 'orbit']

    @staticmethod
    def _candidates(name: str, cfg: Dict) -> List[str]:
        """Possible MCP URLs for one module, best-guess first. Configs
        declare this every way imaginable — a string URL under urls.mcp or
        endpoints.mcp, an `mcp` block with a url, or just a block of prose
        plus an api port. The probe sorts truth from prose."""
        urls, seen = [], set()

        def add(u):
            if isinstance(u, str) and u.startswith('http') and u not in seen:
                seen.add(u)
                urls.append(u)

        blocks = (cfg.get('urls') or {}, cfg.get('endpoints') or {})
        for block in blocks:
            if isinstance(block, dict):
                add(block.get('mcp'))
        mcp = cfg.get('mcp')
        if isinstance(mcp, dict):
            for k in ('url', 'connect', 'http'):
                add(mcp.get(k))
        elif isinstance(mcp, str):
            add(mcp)
        # derived fallback: {api}/mcp — the fleet convention
        api = None
        if isinstance(cfg.get('urls'), dict):
            api = cfg['urls'].get('api')
        if not api:
            port = cfg.get('port') or (cfg.get('ports') or {}).get('api') \
                if isinstance(cfg.get('ports'), dict) else cfg.get('port')
            if isinstance(port, int):
                api = f"http://localhost:{port}"
        if isinstance(api, str) and api.startswith('http'):
            add(api.rstrip('/') + '/mcp')
        return urls

    def discover(self) -> Dict[str, List[str]]:
        """name -> candidate URLs, for every module whose config mentions
        MCP at all. File reads only — the network happens in refresh()."""
        found = {}
        for root in self._roots:
            if not root.is_dir():
                continue
            for cfg_path in sorted(root.glob('*/config.json')):
                name = cfg_path.parent.name
                if name in SKIP or name.startswith(('_', '.')) or name in found:
                    continue
                try:
                    cfg = json.loads(cfg_path.read_text())
                except Exception:
                    continue
                if not any(k in cfg for k in ('mcp',)) and not any(
                        isinstance(cfg.get(b), dict) and cfg[b].get('mcp')
                        for b in ('urls', 'endpoints')):
                    continue
                urls = self._candidates(name, cfg)
                if urls:
                    found[name] = urls
        return found

    # ── probing + the persisted index ────────────────────────────────

    def _auth_headers(self, server: str) -> Dict[str, str]:
        """Optional per-server headers from ~/.mod/agent/mcp/auth.json —
        {"build": {"Authorization": "Bearer …"}} or {"build": "<token>"}."""
        try:
            auth = json.loads((self._dir / 'auth.json').read_text())
        except Exception:
            return {}
        entry = auth.get(server)
        if isinstance(entry, str):
            return {'Authorization': f'Bearer {entry}'}
        return entry if isinstance(entry, dict) else {}

    def _probe(self, name: str, urls: List[str], wake: bool = False) -> Dict:
        """Handshake the candidates in order; first tools/list wins."""
        if wake:
            urls = urls + [ACTIVATOR.format(mod=name)]
        last_err = 'no candidate urls'
        for url in urls:
            client = Client(url, headers=self._auth_headers(name))
            try:
                info = client.handshake()
                tools = client.tools()
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"[:300]
                continue
            self._clients[name] = client
            return {
                'url': url, 'ok': True, 'error': None, 'fetched': time.time(),
                'server': (info.get('serverInfo') or {}).get('name') or name,
                'tools': [{
                    'name': t.get('name', ''),
                    'description': _clip(t.get('description') or
                                         t.get('title') or ''),
                    'inputSchema': t.get('inputSchema') or {},
                } for t in tools if t.get('name')],
            }
        return {'url': urls[0] if urls else None, 'ok': False,
                'error': last_err, 'fetched': time.time(), 'tools': []}

    def refresh(self, server: str = None, wake: bool = None) -> Dict[str, Any]:
        """Re-discover and re-probe the fleet (or one server, woken by
        default — an explicit ask is worth starting a sleeping mod for)."""
        if os.environ.get('AGENT_MCP_DISABLE'):
            return {'servers': 0, 'tools': 0, 'disabled': True}
        found = self.discover()
        if server:
            urls = found.get(server)
            if not urls:
                raise KeyError(f"no MCP declaration found for module: {server}")
            entry = self._probe(server, urls,
                                wake=True if wake is None else wake)
            with self._lock:
                self._index[server] = entry
                self._save()
            return {'server': server, **{k: entry[k] for k in
                                         ('url', 'ok', 'error')},
                    'tools': len(entry['tools'])}
        index = {}
        with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
            futures = {pool.submit(self._probe, n, u, bool(wake)): n
                       for n, u in found.items()}
            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    index[name] = fut.result()
                except Exception as e:
                    index[name] = {'url': None, 'ok': False, 'tools': [],
                                   'error': str(e)[:300],
                                   'fetched': time.time()}
        with self._lock:
            # a server that answered before keeps its old catalog through a
            # bad probe — stale beats gone (restart races, rebuilds, …)
            for name, entry in index.items():
                old = self._index.get(name)
                if not entry['ok'] and old and old.get('ok'):
                    entry = {**old, 'error': entry['error']}
                self._index[name] = entry
            self._index = {n: e for n, e in self._index.items() if n in found}
            self._read = time.time()
            self._save()
        ok = [n for n, e in self._index.items() if e.get('ok')]
        return {'servers': len(self._index), 'online': len(ok),
                'tools': sum(len(e['tools']) for e in self._index.values())}

    def _load(self):
        try:
            data = json.loads((self._dir / 'index.json').read_text())
            self._index = data.get('servers') or {}
            self._read = data.get('fetched') or 0.0
        except Exception:
            pass

    def _save(self):
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            (self._dir / 'index.json').write_text(json.dumps(
                {'fetched': self._read, 'servers': self._index}, indent=1))
        except Exception:
            pass   # a cache that can't persist is still a cache

    def index(self, fresh: bool = False) -> Dict[str, Dict]:
        """server -> probed entry. Auto-refreshes when the cache has aged
        out, with a cooldown so a dead fleet isn't re-probed per lookup."""
        now = time.time()
        if fresh or ((now - self._read) > self._ttl and
                     (now - self._last_attempt) > RETRY_COOLDOWN):
            self._last_attempt = now
            try:
                self.refresh()
            except Exception:
                pass   # stale index beats a raised lookup
        return self._index

    # ── names ────────────────────────────────────────────────────────

    @staticmethod
    def tool_name(server: str, tool: str) -> str:
        return f"{PREFIX}{server}.{tool}"

    @staticmethod
    def split(name: str) -> (str, str):
        """'mcp.build.list_modules' -> ('build', 'list_modules'). The server
        is the first segment — a module name never contains a dot, the MCP
        tool name after it may be anything."""
        rest = name[len(PREFIX):] if name.startswith(PREFIX) else name
        server, _, tool = rest.partition('.')
        return server, tool

    def ls(self, fresh: bool = False) -> List[str]:
        """Every MCP tool on every reachable fleet server."""
        return [self.tool_name(s, t['name'])
                for s, e in self.index(fresh).items() if e.get('ok')
                for t in e['tools']]

    def exists(self, name: str) -> bool:
        if not name.startswith(PREFIX):
            return False
        server, tool = self.split(name)
        entry = self._index.get(server) or self.index().get(server)
        return bool(entry and tool and
                    any(t['name'] == tool for t in entry.get('tools', [])))

    def _tool(self, server: str, tool: str) -> Optional[Dict]:
        entry = self.index().get(server)
        if not entry:
            return None
        return next((t for t in entry['tools'] if t['name'] == tool), None)

    def get(self, name: str) -> Dict[str, Any]:
        server, tool = self.split(name)
        found = self._tool(server, tool)
        if found is None:
            raise KeyError(f"MCP tool not found: {name}")
        return {'name': self.tool_name(server, tool), 'server': server,
                'tool': tool, 'description': found['description'],
                'inputSchema': found['inputSchema'],
                'url': self.index()[server].get('url'), 'kind': 'mcp'}

    def servers(self, fresh: bool = False) -> List[Dict[str, Any]]:
        """Console view: one row per fleet MCP server."""
        return [{'name': n, 'url': e.get('url'), 'ok': bool(e.get('ok')),
                 'error': e.get('error'), 'tools': len(e.get('tools', [])),
                 'fetched': e.get('fetched')}
                for n, e in sorted(self.index(fresh).items())]

    def items(self, q: str = '', limit: int = None) -> List[Dict[str, Any]]:
        """Console listing of tools, narrowed server-side — the catalog is
        hundreds of entries, same deal as the fleet registry."""
        q = (q or '').strip().lower()
        out = []
        for server, entry in sorted(self.index().items()):
            if not entry.get('ok'):
                continue
            for t in entry['tools']:
                name = self.tool_name(server, t['name'])
                if q and q not in name.lower() and \
                        q not in t['description'].lower():
                    continue
                out.append({'name': name, 'server': server, 'kind': 'mcp',
                            'description': t['description']})
                if limit and len(out) >= limit:
                    return out
        return out

    # ── the LLM view ─────────────────────────────────────────────────

    @staticmethod
    def _params(input_schema: Dict) -> Dict[str, Dict]:
        """MCP inputSchema (JSON Schema) -> the params shape the built-ins
        produce, so all four tool kinds read identically in the prompt."""
        props = (input_schema or {}).get('properties') or {}
        required = set((input_schema or {}).get('required') or [])
        params = {}
        for pname, spec in props.items():
            if not isinstance(spec, dict):
                spec = {}
            p = {'type': spec.get('type', 'Any'), 'required': pname in required}
            hint = spec.get('description') or ''
            if spec.get('enum'):
                hint = (hint + ' ' if hint else '') + \
                       'one of: ' + ', '.join(map(str, spec['enum'][:12]))
            if hint:
                p['hint'] = _clip(hint, MAX_HINT)
            if 'default' in spec:
                p['default'] = spec['default']
            params[pname] = p
        return params

    def schema(self, names: List[str] = None) -> Dict[str, Dict]:
        names = self.ls() if names is None else [n for n in names if self.exists(n)]
        out = {}
        for name in names:
            server, tool = self.split(name)
            found = self._tool(server, tool)
            if found is None:
                continue
            out[name] = {
                'description': f"{server} module (MCP) — {found['description']}",
                'params': self._params(found['inputSchema']),
                'server': server,
                'kind': 'mcp',
            }
        return out

    # ── calling ──────────────────────────────────────────────────────

    def _client(self, server: str) -> Client:
        client = self._clients.get(server)
        if client is None:
            entry = self.index().get(server) or {}
            url = entry.get('url')
            if not url:
                raise KeyError(f"MCP server not indexed: {server}")
            client = Client(url, headers=self._auth_headers(server))
            self._clients[server] = client
        return client

    @staticmethod
    def _unpack(result: Dict) -> Dict[str, Any]:
        """tools/call result -> {success, result}. structuredContent wins
        when present; text blocks join; isError means the tool itself said
        no, which the loop should read as a failed step."""
        is_error = bool(result.get('isError'))
        if 'structuredContent' in result and result['structuredContent'] is not None:
            payload = result['structuredContent']
        else:
            blocks = result.get('content') or []
            texts = [b.get('text', '') for b in blocks
                     if isinstance(b, dict) and b.get('type') == 'text']
            payload = '\n'.join(texts) if texts else blocks
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = payload[:MAX_OUTPUT]
        key = 'error' if is_error else 'result'
        return {'success': not is_error, key: _jsonable(payload)}

    def run(self, name: str, params: Dict = None, **kwargs) -> Dict[str, Any]:
        """Call one MCP tool. Arguments arrive nested (`params={…}`) or flat
        (`q='x'`) — both work, same accommodation ModTools makes."""
        server, tool = self.split(name)
        if self._tool(server, tool) is None:
            return {'success': False,
                    'error': f"MCP tool not found: {name}. "
                             f"Known servers: {', '.join(sorted(self._index)) or 'none'}"}
        args = {**(params if isinstance(params, dict) else {}), **kwargs}
        try:
            client = self._client(server)
            try:
                result = client.call(tool, args)
            except (urllib.error.URLError, McpError, OSError):
                # connection refused usually means the mod is asleep — a real
                # tool call is worth waking it through the activator
                woken = Client(ACTIVATOR.format(mod=server),
                               headers=self._auth_headers(server))
                woken.handshake(timeout=CALL_TIMEOUT)
                result = woken.call(tool, args)
                self._clients[server] = woken
            return {'server': server, 'tool': tool, **self._unpack(result)}
        except Exception as e:
            return {'success': False, 'server': server, 'tool': tool,
                    'error': f"{type(e).__name__}: {e}"[:2000]}

    def forward(self, name: str = None, **kwargs) -> Any:
        """Mod protocol entry point.

        forward()                                  -> servers + counts
        forward("mcp.build.list_modules")          -> one tool
        forward(action="run", name=…, params=…)    -> call it
        forward(action="refresh"[, server=…])      -> re-probe the fleet
        forward(action="tools", q=…, limit=…)      -> search the catalog
        """
        action = kwargs.get('action')
        if action == 'run':
            return self.run(kwargs.get('name', name or ''),
                            kwargs.get('params'))
        if action == 'refresh':
            return self.refresh(kwargs.get('server'), kwargs.get('wake'))
        if action == 'tools':
            items = self.items(kwargs.get('q', ''), kwargs.get('limit'))
            return {'tools': items, 'total': len(items)}
        if name is None:
            rows = self.servers()
            return {'servers': rows,
                    'online': sum(1 for r in rows if r['ok']),
                    'tools': sum(r['tools'] for r in rows)}
        return self.get(name)

    def test(self) -> Dict[str, Any]:
        assert self.split('mcp.build.list_modules') == ('build', 'list_modules')
        assert self.split('mcp.x.a.b') == ('x', 'a.b')
        assert self.tool_name('dns', 'dns_resolve') == 'mcp.dns.dns_resolve'
        assert not self.exists('bash') and not self.exists('mod.git')
        params = self._params({'properties': {
            'q': {'type': 'string', 'description': 'query'},
            'n': {'type': 'integer', 'default': 5}}, 'required': ['q']})
        assert params['q']['required'] and not params['n']['required']
        assert params['n']['default'] == 5
        unpacked = self._unpack({'content': [{'type': 'text',
                                              'text': '{"a": 1}'}]})
        assert unpacked == {'success': True, 'result': {'a': 1}}
        assert not self._unpack({'isError': True, 'content': []})['success']
        found = self.discover()
        servers = self.servers()
        return {'passed': True, 'declared': len(found),
                'indexed': len(servers),
                'tools': sum(r['tools'] for r in servers)}
