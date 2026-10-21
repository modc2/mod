"""
x402 - every paid (HTTP 402) API in the world, as tools

The orbit/x402 module indexes every x402 service the public facilitators list
(tens of thousands) and serves them over MCP: search by meaning, quote, call,
pay. Its own MCP tools already reach the agent as `mcp.x402.x402_find` and
friends — but "search, then call a URL" is two hops and a URL the model has
to carry around. This registry is the direct door: a service you MOUNT
becomes a first-class tool, `x402.<slug>`, with the input schema the listing
publishes (bazaar `queryParams` / `bodyFields`), its price in the description,
and one call that goes through x402's `x402_call` — so the trust ledger, the
budget and the payment code all stay x402's own.

Paid MCP servers the index found (x402_mcp) mount the same way: every one of
their tools is a priced HTTP endpoint, so mounting a server mounts each tool
as `x402.<server>.<tool>`.

Paying is opt-in per mount. `pay` is passed straight to x402_call:
    none   (default) — unpaid call; a 402 comes back as the offers + price
    auto           — x402 pays with the node (house) wallet if its policy and
                     the caller's trust allow, never above max_price_usd
    house          — same, but only the house wallet
The agent always reaches x402 over loopback, which x402 reads as its local
owner — so a mount that says `auto` spends the owner's budget. That is why
mounting is an owner action at the API, and why max_price_usd defaults low.

State OFF-tree: ~/.mod/agent/x402/mounts.json.

Usage:
    x = X402Tools()
    x.find('weather forecast')                    # live search, mounted flag
    x.mount('https://…/forecast')                 # -> x402.automatom-forecast
    x.mount_server('https://mcp.cryptoiz.org')    # every tool on that host
    x.run('x402.automatom-forecast', lat='40.7', lon='-74.0')
"""
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..mcp.mod import Client, McpError, ACTIVATOR, _clip, _jsonable

PREFIX = 'x402.'
STATE_DIR = Path(os.path.expanduser('~/.mod/agent/x402'))
DEFAULT_URL = 'http://127.0.0.1:51110/mcp'
PAY_MODES = ('none', 'auto', 'house')
DEFAULT_MAX_PRICE = float(os.environ.get('AGENT_X402_MAX_PRICE', 0.05))
MAX_MOUNTS_PER_SERVER = 60
MAX_SLUG = 48


def slugify(text: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-')
    return s[:MAX_SLUG].strip('-') or 'service'


def _price(p) -> str:
    try:
        return f"${float(p):.4g}/call"
    except (TypeError, ValueError):
        return 'price unknown'


def _input_spec(raw: Dict) -> Dict[str, Any]:
    """Pull the request shape out of a listing, whichever way it was
    published: v2 bazaar extension (JSON schema, then example values),
    a top-level inputSchema, or v1's accepts[].outputSchema.input.
    Returns {'method': str|None, 'params': {name: {type, required, hint,
    where}}}, where `where` is 'query' or 'body'."""
    raw = raw if isinstance(raw, dict) else {}
    method, params = None, {}

    def take(props: Dict, required, where: str):
        if not isinstance(props, dict):
            return
        req = set(required or []) if isinstance(required, list) else set()
        for k, v in props.items():
            if k in params:
                continue
            if isinstance(v, dict):
                p = {'type': v.get('type', 'string'), 'required': k in req,
                     'where': where}
                hint = v.get('description') or ''
                if v.get('enum'):
                    hint = (hint + ' ' if hint else '') + 'one of: ' + \
                        ', '.join(map(str, v['enum'][:12]))
                if 'example' in v:
                    hint = (hint + ' ' if hint else '') + f"e.g. {v['example']}"
            else:
                # an example value, not a schema: required by being shown
                p = {'type': 'string' if isinstance(v, str) else
                     type(v).__name__ if v is not None else 'string',
                     'required': bool(required is True), 'where': where}
                hint = f"e.g. {v}" if v not in (None, '') else ''
            if hint:
                p['hint'] = _clip(str(hint), 200)
            params[k] = p

    bazaar = ((raw.get('extensions') or {}).get('bazaar') or {})
    schema_in = (((bazaar.get('schema') or {}).get('properties') or {})
                 .get('input') or {}).get('properties') or {}
    for where, key in (('query', 'queryParams'), ('body', 'bodyFields'),
                       ('body', 'body')):
        blk = schema_in.get(key) or {}
        take(blk.get('properties'), blk.get('required'), where)
    m = (schema_in.get('method') or {}).get('enum')
    if isinstance(m, list) and m:
        method = m[0]

    examples = [((bazaar.get('info') or {}).get('input') or {}),
                raw.get('inputSchema') or {}]
    for a in raw.get('accepts') or []:
        if isinstance(a, dict):
            examples.append(((a.get('outputSchema') or {}).get('input') or {}))
    for ex in examples:
        if not isinstance(ex, dict):
            continue
        method = method or ex.get('method')
        # an example block with no schema behind it: every key it shows is
        # what a working request looked like, so treat them as required
        none_yet = not params
        take(ex.get('queryParams'), True if none_yet else None, 'query')
        take(ex.get('bodyFields'), True if none_yet else None, 'body')
    return {'method': method, 'params': params}


class X402Tools:
    """Mounted x402 services as a tool registry — `x402.<slug>`."""
    description = ("x402 registry - paid HTTP APIs (and paid MCP servers) from "
                   "the x402 index, mounted as callable tools")

    def __init__(self, state_dir: str = None, url: str = None, mcp=None,
                 **kwargs):
        self._dir = Path(state_dir) if state_dir else STATE_DIR
        self._url = url
        self._mcp = mcp                # the McpTools registry, for its index
        self._lock = threading.Lock()
        self._client: Optional[Client] = None
        self._mounts: Dict[str, Dict] = {}
        self._load()

    # ── the x402 module ──────────────────────────────────────────────

    def endpoint(self) -> str:
        """Where x402's MCP answers: explicit > env > the probed fleet index
        > its config.json > the default port."""
        if self._url:
            return self._url
        env = os.environ.get('AGENT_X402_URL')
        if env:
            return env
        try:
            entry = (self._mcp._index or {}).get('x402') if self._mcp else None
            if entry and entry.get('ok') and entry.get('url'):
                return entry['url']
        except Exception:
            pass
        try:
            cfg_path = Path(__file__).resolve().parents[4].parent / 'x402' / 'config.json'
            cfg = json.loads(cfg_path.read_text())
            port = cfg.get('port')
            if isinstance(port, int):
                return f"http://127.0.0.1:{port}/mcp"
        except Exception:
            pass
        return DEFAULT_URL

    def _call(self, tool: str, args: Dict) -> Any:
        """One x402 MCP tool call, unpacked to its JSON payload. Wakes x402
        through the activator when its port refuses (scaled to zero)."""
        def go(client):
            res = client.call(tool, args)
            blocks = res.get('content') or []
            text = '\n'.join(b.get('text', '') for b in blocks
                             if isinstance(b, dict) and b.get('type') == 'text')
            payload = res.get('structuredContent')
            if payload is None:
                try:
                    payload = json.loads(text) if text else blocks
                except Exception:
                    payload = text
            if res.get('isError'):
                raise McpError(payload if isinstance(payload, str)
                               else json.dumps(payload)[:2000])
            return payload

        if self._client is None:
            self._client = Client(self.endpoint())
        try:
            return go(self._client)
        except McpError:
            raise
        except Exception:
            woken = Client(ACTIVATOR.format(mod='x402'))
            woken.handshake()
            self._client = woken
            return go(woken)

    # ── mounts (persisted) ───────────────────────────────────────────

    def _load(self):
        try:
            data = json.loads((self._dir / 'mounts.json').read_text())
            self._mounts = data.get('mounts') or {}
        except Exception:
            self._mounts = {}

    def _save(self):
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._dir / 'mounts.json.tmp'
            tmp.write_text(json.dumps({'mounts': self._mounts}, indent=1))
            tmp.replace(self._dir / 'mounts.json')
        except Exception:
            pass

    def _free_slug(self, base: str, url: str) -> str:
        slug, n = base, 2
        while slug in self._mounts and self._mounts[slug].get('url') != url:
            slug = f"{base}-{n}"
            n += 1
        return slug

    @staticmethod
    def suggest(row: Dict) -> str:
        """A readable slug for a listing: its name, else host + last path
        segment (automatom.matt-d59.workers.dev/forecast -> automatom-forecast)."""
        if row.get('name'):
            return slugify(row['name'])
        u = urlparse(row.get('url') or '')
        host = (u.hostname or '').split('.')
        head = host[0] if host and host[0] not in ('www', 'api', 'mcp') else \
            (host[1] if len(host) > 1 else 'svc')
        tail = [p for p in (u.path or '').split('/') if p and not p.startswith(':')]
        return slugify('-'.join([head] + tail[-2:]))

    def mount(self, url: str, name: str = None, description: str = None,
              method: str = None, pay: str = 'none',
              max_price_usd: float = None, params: Dict = None,
              server: str = None) -> Dict[str, Any]:
        """Make one x402 service a tool. Reads its listing from the index
        (description, price, networks, input schema); anything passed here
        overrides it. Re-mounting the same URL updates it in place."""
        if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
            raise ValueError(f"not a service URL: {url!r}")
        if pay not in PAY_MODES:
            raise ValueError(f"pay must be one of {', '.join(PAY_MODES)}")
        try:
            row = self._call('x402_service', {'url': url, 'raw': True})
        except Exception as e:
            row = {'url': url, '_error': str(e)[:300]}
        if not isinstance(row, dict):
            row = {'url': url}
        spec = _input_spec(row.get('raw') or {})
        base = slugify(name) if name else self.suggest(row)
        if server:
            base = f"{slugify(server)}.{base}"
        with self._lock:
            existing = next((k for k, v in self._mounts.items()
                             if v.get('url') == url), None)
            slug = existing if existing and not name else self._free_slug(base, url)
            if existing and existing != slug:
                self._mounts.pop(existing, None)
            entry = {
                'url': url,
                'method': (method or row.get('method') or spec['method']
                           or 'GET').upper(),
                'description': _clip(description or row.get('description')
                                     or row.get('name') or url, 400),
                'host': row.get('host') or urlparse(url).hostname,
                'price_usd': row.get('price_usd'),
                'networks': row.get('networks') or [],
                'params': params if isinstance(params, dict) else spec['params'],
                'pay': pay,
                'max_price_usd': DEFAULT_MAX_PRICE if max_price_usd is None
                else float(max_price_usd),
                'server': server,
                'mounted': time.time(),
                'indexed': '_error' not in row,
            }
            self._mounts[slug] = entry
            self._save()
        return {'name': PREFIX + slug, **entry}

    def mount_server(self, server: str, pay: str = 'none',
                     max_price_usd: float = None,
                     limit: int = MAX_MOUNTS_PER_SERVER) -> Dict[str, Any]:
        """Mount every service one host offers — the way a paid MCP server
        from x402_mcp arrives: each of its tools is a priced endpoint."""
        host = urlparse(server).hostname if '://' in server else server
        if not host:
            raise ValueError(f"not a server: {server!r}")
        res = self._call('x402_search', {'host': host, 'limit': int(limit)})
        rows = (res or {}).get('items') or []
        if not rows:
            raise KeyError(f"x402 lists no services on {host}")
        label = host.split('.')[0] if host.split('.')[0] not in ('www', 'api', 'mcp') \
            else host.split('.')[1]
        mounted = []
        for row in rows[:limit]:
            tail = [p for p in urlparse(row['url']).path.split('/')
                    if p and not p.startswith(':')]
            nm = row.get('name') or '-'.join(tail[-2:]) or 'root'
            mounted.append(self.mount(row['url'], name=nm, server=label, pay=pay,
                                      max_price_usd=max_price_usd)['name'])
        return {'server': host, 'mounted': mounted, 'total': len(mounted)}

    def unmount(self, name: str) -> Dict[str, Any]:
        """Drop a mount (by tool name, slug, URL, or `server:<label>` for all
        of one server's tools)."""
        key = name[len(PREFIX):] if name.startswith(PREFIX) else name
        with self._lock:
            if key.startswith('server:'):
                label = key[7:]
                gone = [k for k, v in self._mounts.items() if v.get('server') == label]
            else:
                gone = [k for k, v in self._mounts.items()
                        if k == key or v.get('url') == name]
            if not gone:
                raise KeyError(f"not mounted: {name}")
            for k in gone:
                self._mounts.pop(k, None)
            self._save()
        return {'removed': [PREFIX + k for k in gone]}

    def set_pay(self, name: str, pay: str = None,
                max_price_usd: float = None) -> Dict[str, Any]:
        key = name[len(PREFIX):] if name.startswith(PREFIX) else name
        if pay is not None and pay not in PAY_MODES:
            raise ValueError(f"pay must be one of {', '.join(PAY_MODES)}")
        with self._lock:
            entry = self._mounts.get(key)
            if not entry:
                raise KeyError(f"not mounted: {name}")
            if pay is not None:
                entry['pay'] = pay
            if max_price_usd is not None:
                entry['max_price_usd'] = float(max_price_usd)
            self._save()
        return {'name': PREFIX + key, **entry}

    # ── search (live, through x402) ──────────────────────────────────

    def find(self, q: str, k: int = 10, mcp: bool = False,
             max_price: float = None, network: str = None) -> Dict[str, Any]:
        """Search the x402 index by meaning. `mcp=True` ranks paid MCP
        servers instead of single endpoints. Every row says whether it is
        already mounted and the tool name it would mount as."""
        args = {'q': q, 'k': int(k)}
        if max_price is not None:
            args['max_price'] = float(max_price)
        if network:
            args['network'] = network
        res = self._call('x402_mcp' if mcp else 'x402_find', args) or {}
        by_url = {v['url']: k for k, v in self._mounts.items()}
        items = []
        for row in res.get('items') or []:
            if mcp:
                host = row.get('host')
                n = sum(1 for v in self._mounts.values() if v.get('host') == host)
                items.append({**row, 'mounted': n})
            else:
                slug = by_url.get(row.get('url'))
                items.append({**row, 'mounted': bool(slug),
                              'tool': PREFIX + (slug or self.suggest(row))})
        return {'q': q, 'kind': 'mcp' if mcp else 'service',
                'mode': res.get('mode'), 'items': items, 'total': len(items)}

    # ── registry surface (same shape as mcp/ and mods/) ──────────────

    def ls(self) -> List[str]:
        return [PREFIX + k for k in sorted(self._mounts)]

    def exists(self, name: str) -> bool:
        return name.startswith(PREFIX) and self._entry(name) is not None

    def _entry(self, name: str) -> Optional[Dict]:
        key = name[len(PREFIX):] if name.startswith(PREFIX) else name
        entry = self._mounts.get(key)
        if entry is None:
            low = key.lower()   # the loop lowercases tool names
            entry = next((v for k, v in self._mounts.items() if k.lower() == low), None)
        return entry

    def get(self, name: str) -> Dict[str, Any]:
        entry = self._entry(name)
        if entry is None:
            raise KeyError(f"x402 tool not mounted: {name}")
        return {'name': name, 'kind': 'x402', **entry}

    def items(self, q: str = '', limit: int = None) -> List[Dict[str, Any]]:
        q = (q or '').strip().lower()
        out = []
        for slug, e in sorted(self._mounts.items()):
            name = PREFIX + slug
            if q and q not in name.lower() and q not in (e.get('description') or '').lower() \
                    and q not in (e.get('host') or '').lower():
                continue
            out.append({'name': name, 'kind': 'x402', 'server': e.get('server'),
                        'host': e.get('host'), 'url': e['url'],
                        'price_usd': e.get('price_usd'), 'pay': e.get('pay'),
                        'max_price_usd': e.get('max_price_usd'),
                        'description': self._describe(e)})
            if limit and len(out) >= limit:
                break
        return out

    @staticmethod
    def _describe(e: Dict) -> str:
        paying = {'none': 'unpaid: a 402 returns the price and offers',
                  'auto': f"pays up to ${e.get('max_price_usd')} from the node wallet",
                  'house': f"pays up to ${e.get('max_price_usd')} from the house wallet"}
        return (f"{e.get('description') or e['url']} "
                f"[x402 · {e.get('host')} · {_price(e.get('price_usd'))} · "
                f"{paying.get(e.get('pay'), '')}]")

    def schema(self, names: List[str] = None) -> Dict[str, Dict]:
        names = self.ls() if names is None else [n for n in names if self.exists(n)]
        out = {}
        for name in names:
            e = self._entry(name)
            params = {k: {kk: vv for kk, vv in v.items() if kk != 'where'}
                      for k, v in (e.get('params') or {}).items()}
            if not params:
                # nothing published: give the model the raw request knobs
                params = {'query': {'type': 'object', 'required': False,
                                    'hint': 'query-string parameters'},
                          'body': {'type': 'object', 'required': False,
                                   'hint': 'JSON body (POST services)'}}
            out[name] = {'description': self._describe(e), 'params': params,
                         'kind': 'x402', 'server': e.get('server') or e.get('host')}
        return out

    # ── calling ──────────────────────────────────────────────────────

    def run(self, name: str, /, params: Dict = None, **kwargs) -> Dict[str, Any]:
        """Call a mounted service through x402_call. Arguments route to the
        query string or the JSON body by where the listing said they go;
        unknown ones follow the method (GET -> query, else body). `query=` /
        `body=` objects are passed through as given."""
        e = self._entry(name)
        if e is None:
            return {'success': False,
                    'error': f"x402 tool not mounted: {name}. Mount one with "
                             f"x402_mount, or search with x402_find."}
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = None
        args = {**(params if isinstance(params, dict) else {}), **kwargs}
        query = dict(args.pop('query', None) or {}) if isinstance(args.get('query'), dict) else {}
        body = args.pop('body', None)
        body = dict(body) if isinstance(body, dict) else body
        spec = e.get('params') or {}
        method = e.get('method') or 'GET'
        for k, v in args.items():
            where = (spec.get(k) or {}).get('where') or ('query' if method == 'GET' else 'body')
            if where == 'body':
                body = body if isinstance(body, dict) else {}
                body[k] = v
            else:
                query[k] = v if isinstance(v, str) else json.dumps(v) \
                    if isinstance(v, (dict, list)) else str(v)
        call = {'url': e['url'], 'method': method, 'pay': e.get('pay') or 'none',
                'max_price_usd': e.get('max_price_usd') or DEFAULT_MAX_PRICE}
        if query:
            call['query'] = query
        if body is not None:
            call['body'] = body
        try:
            res = self._call('x402_call', call)
        except Exception as ex:
            return {'success': False, 'tool': name, 'url': e['url'],
                    'error': f"{type(ex).__name__}: {ex}"[:2000]}
        status = res.get('status') if isinstance(res, dict) else None
        ok = isinstance(status, int) and 200 <= status < 300
        out = {'success': ok, 'tool': name, 'url': e['url'], 'status': status}
        if ok:
            body_out = next((res[k] for k in ('json', 'text', 'base64') if k in res), res)
            out['result'] = _jsonable(body_out)
            if (res.get('paid') or {}).get('by') or res.get('receipt'):
                out['payment'] = _jsonable({**(res.get('paid') or {}),
                                            'receipt': res.get('receipt')})
        else:
            # a 402 under pay=none is the expected answer, not a crash: it
            # carries the price, and the model should report it, not retry
            out['error'] = (f"payment required ({_price(e.get('price_usd'))}); this "
                            f"mount is pay={e.get('pay')}"
                            + (f" — {res.get('note') or res.get('payment_error')}"
                               if res.get('note') or res.get('payment_error') else '')
                            if status == 402 else f"HTTP {status}")
            out['detail'] = _jsonable(res)
        return out

    def forward(self, name: str = None, **kwargs) -> Any:
        """Mod protocol entry point.

        forward()                                   -> mounts
        forward(action="find", q=…, mcp=…)          -> search x402
        forward(action="mount", url=…, pay=…)       -> mount one service
        forward(action="mount_server", server=…)    -> mount a whole host
        forward(action="unmount", name=…)           -> drop
        forward(action="run", name=…, params=…)     -> call
        """
        action = kwargs.pop('action', None)
        if action == 'find':
            return self.find(kwargs.get('q', ''), kwargs.get('k', 10),
                             bool(kwargs.get('mcp')), kwargs.get('max_price'),
                             kwargs.get('network'))
        if action == 'mount':
            return self.mount(**{k: v for k, v in kwargs.items() if k in (
                'url', 'name', 'description', 'method', 'pay', 'max_price_usd',
                'params')})
        if action == 'mount_server':
            return self.mount_server(kwargs.get('server', ''), kwargs.get('pay', 'none'),
                                     kwargs.get('max_price_usd'))
        if action == 'unmount':
            return self.unmount(kwargs.get('name', name or ''))
        if action == 'run':
            return self.run(kwargs.get('name', name or ''), kwargs.get('params'))
        if name is None:
            items = self.items()
            return {'tools': items, 'total': len(items), 'endpoint': self.endpoint()}
        return self.get(name)

    def test(self) -> Dict[str, Any]:
        assert slugify('Weather Forecast · London!') == 'weather-forecast-london'
        assert self.suggest({'url': 'https://automatom.x.dev/forecast'}) == 'automatom-forecast'
        spec = _input_spec({'extensions': {'bazaar': {'schema': {'properties': {'input': {
            'properties': {'queryParams': {'properties': {'lat': {'type': 'string'}},
                                           'required': ['lat']}}}}}}}})
        assert spec['params']['lat'] == {'type': 'string', 'required': True, 'where': 'query'}
        return {'passed': True, 'mounted': len(self._mounts), 'endpoint': self.endpoint()}
