"""targets — the four things a step can call, behind one signature.

Everything here returns plain data. That is the contract the rest of the module
is built on: a step's output has to be something the next step's `${...}` can
index into, so an MCP envelope is unwrapped, an HTTP body is parsed, and a mod
fn's return value is left alone.

    mcp   a tool on any MCP server in the fleet, through the hub at :50360
    mod   a python mod fn — m.mod('shelf')().space()
    http  any URL, including a mod's own REST route
    expr  no call at all: reshape what upstream steps already returned
"""

import json
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from . import refs

HUB = os.environ.get('DAG_MCP_HUB', 'http://127.0.0.1:50360')
ACTIVATOR = os.environ.get('DAG_ACTIVATOR_URL', 'http://127.0.0.1:9000')
MOD_ROOT = os.environ.get('MOD_ROOT', '/root/mod/mod')
TIMEOUT = float(os.environ.get('DAG_STEP_TIMEOUT', 120))


class StepError(Exception):
    """A step failed for a reason worth printing. `kind` groups the reasons so
    a run record can say 'three steps failed, all of them the same way'."""

    def __init__(self, message, kind='error', detail=None):
        super().__init__(message)
        self.kind, self.detail = kind, detail

    def dict(self):
        d = {'error': str(self), 'kind': self.kind}
        if self.detail is not None:
            d['detail'] = self.detail
        return d


# ── http ─────────────────────────────────────────────────────────

def request(url, method='GET', body=None, headers=None, timeout=TIMEOUT):
    data = None
    headers = dict(headers or {})
    if body is not None and method not in ('GET', 'HEAD'):
        data = json.dumps(body, default=str).encode()
        headers.setdefault('content-type', 'application/json')
    headers.setdefault('accept', 'application/json, text/event-stream')
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            ctype = r.headers.get('content-type', '')
            return _decode(raw, ctype), r.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        payload = _decode(raw, e.headers.get('content-type', ''))
        raise StepError(f'HTTP {e.code} from {url}', kind='http', detail=payload)
    except urllib.error.URLError as e:
        raise StepError(f'cannot reach {url} — {e.reason}', kind='unreachable')
    except socket.timeout:
        raise StepError(f'{url} did not answer within {timeout:g}s', kind='timeout')


def _decode(raw, ctype=''):
    text = raw.decode('utf-8', 'replace')
    if 'text/event-stream' in ctype:
        # Streamable HTTP: the JSON-RPC response arrives as one SSE data: line.
        for line in text.splitlines():
            if line.startswith('data:'):
                try:
                    return json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def http(step, args):
    url = args.get('url') or step.url
    method = step.method or ('POST' if (step.body is not None or args.get('body'))
                             else 'GET')
    body = args.get('body', step.body)
    params = {k: v for k, v in (args.get('args') or {}).items()}
    if params and method in ('GET', 'HEAD'):
        sep = '&' if '?' in url else '?'
        url += sep + urllib.parse.urlencode(
            {k: v if isinstance(v, str) else json.dumps(v, default=str)
             for k, v in params.items()})
    elif params and body is None:
        body = params
    out, _ = request(url, method, body, args.get('headers'), args.get('timeout'))
    return out


# ── mcp ──────────────────────────────────────────────────────────

def unwrap(result, tool):
    """An MCP tool result -> the data inside it.

    A tools/call answer is an envelope: content blocks, an isError flag, and
    sometimes structuredContent. A downstream step wants the payload, so the
    envelope is opened here rather than in every graph that ever indexes into
    a result. isError becomes a raised StepError, because a graph that keeps
    going on a failed call is a graph that reports nonsense with confidence.
    """
    if not isinstance(result, dict):
        return result
    content = result.get('content')
    texts = [b.get('text', '') for b in content
             if isinstance(b, dict) and b.get('type') == 'text'] \
        if isinstance(content, list) else []
    payload = result.get('structuredContent')
    if payload is None and texts:
        joined = '\n'.join(texts)
        try:
            payload = json.loads(joined)
        except json.JSONDecodeError:
            payload = joined
    if payload is None:
        payload = content if content is not None else result
    if result.get('isError'):
        why = payload
        if isinstance(payload, dict):
            why = payload.get('error') or payload.get('message') or payload
        raise StepError(f'{tool} returned an error: '
                        f'{why if isinstance(why, str) else json.dumps(why, default=str)[:400]}',
                        kind='tool', detail=payload)
    return payload


def _jsonrpc(url, method, params, headers=None, timeout=TIMEOUT, id_=1):
    out, _ = request(url, 'POST', {'jsonrpc': '2.0', 'id': id_, 'method': method,
                                   'params': params or {}}, headers, timeout)
    if not isinstance(out, dict):
        raise StepError(f'{url} answered {method} with {type(out).__name__}, '
                        'not a JSON-RPC object', kind='protocol', detail=out)
    if out.get('error'):
        err = out['error']
        raise StepError(f'{url} refused {method}: '
                        f'{err.get("message") if isinstance(err, dict) else err}',
                        kind='protocol', detail=err)
    return out.get('result')


def mcp_direct(url, tool, args, headers=None, timeout=TIMEOUT):
    """Speak MCP straight at a server, no hub in the middle. Used for a URL the
    hub does not carry, and as the fallback when the hub itself is down."""
    _jsonrpc(url, 'initialize', {'protocolVersion': '2025-06-18',
                                 'capabilities': {},
                                 'clientInfo': {'name': 'dag', 'version': '1'}},
             headers, timeout)
    result = _jsonrpc(url, 'tools/call', {'name': tool, 'arguments': args},
                      headers, timeout, id_=2)
    return unwrap(result, tool)


def mcp(step, args, depth=0):
    """One MCP tool call. Through the hub unless the step names a url."""
    tool = args.get('tool') or step.tool
    server = args.get('server') or step.server
    url = args.get('url') or step.url
    timeout = float(args.get('timeout') or step.timeout or TIMEOUT)
    if server and '__' not in str(tool):
        full = f'{server}__{tool}'
    else:
        full = str(tool)
    if depth >= int(os.environ.get('DAG_MAX_DEPTH', 3)) and full.startswith('dag__'):
        raise StepError(f'{full} at depth {depth} — a graph calling this module '
                        'calling a graph has to stop somewhere', kind='depth')

    if url:
        return mcp_direct(url, str(tool).split('__')[-1], args.get('args') or {},
                          args.get('headers'), timeout)

    body = {'tool': full, 'args': args.get('args') or {}}
    try:
        out, _ = request(f'{HUB}/call', 'POST', body,
                         {'x-dag-depth': str(depth)}, timeout)
    except StepError as e:
        if e.kind == 'unreachable':
            raise StepError(
                f'the MCP hub at {HUB} is not answering, so {full} cannot be '
                'routed. Start it (m mcp/serve) or give the step an explicit '
                'url=', kind='hub')
        if e.kind == 'http' and isinstance(e.detail, dict):
            msg = e.detail.get('error') or e.detail.get('message') or e.detail
            raise StepError(f'{full}: {msg}', kind='tool', detail=e.detail)
        raise
    result = out.get('result') if isinstance(out, dict) and 'result' in out else out
    return unwrap(result, full)


def tool_index(timeout=20):
    """Every tool the hub aggregates, by name. Used to check a graph before it
    runs — 'no tool named that' is worth knowing at parse time, not at step 7."""
    out, _ = request(f'{HUB}/tools', timeout=timeout)
    tools = out.get('tools') if isinstance(out, dict) else out
    return {t['name']: t for t in (tools or []) if isinstance(t, dict) and t.get('name')}


def servers(timeout=20):
    out, _ = request(f'{HUB}/servers', timeout=timeout)
    return out.get('servers') if isinstance(out, dict) else out


# ── mod ──────────────────────────────────────────────────────────

_protocol = None


def protocol():
    """The `mod` package — carefully.

    This directory holds a mod.py, and any module that imports us may have put
    it on sys.path. `import mod` would then get this file instead of the
    protocol, so the import is done with our own directories taken out of the
    way and the result checked for the thing only the real package has.
    """
    global _protocol
    if _protocol is not None:
        return _protocol
    import importlib
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    mine = {here, os.path.dirname(here)}
    saved_path, saved_mod = list(sys.path), sys.modules.pop('mod', None)
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or '.') not in mine]
    root = os.path.dirname(MOD_ROOT.rstrip('/'))
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        m = importlib.import_module('mod')
        if not hasattr(m, 'mod'):
            raise StepError('imported a `mod` that is not the protocol package '
                            f'({getattr(m, "__file__", "?")})', kind='import')
        _protocol = m
        return m
    except ImportError as e:
        raise StepError(f'the mod protocol package will not import: {e}',
                        kind='import')
    finally:
        sys.path[:] = saved_path
        if saved_mod is not None:
            sys.modules['mod'] = saved_mod


def mod(step, args):
    """Call `<mod>/<fn>` in-process. Falls back to the gateway when the module
    is not importable here (a Rust or Next module has no python fn to call).

    sys.path is restored afterwards. Modules in this fleet routinely put their
    own directory at the front of it to reach a local package, and several of
    them call that package `src`; leaving one module's path edit in place makes
    the NEXT module's import resolve into the wrong tree. (This module's own
    package is `dagsrc` for the same reason — a top-level `src` is a name three
    other modules already own.)
    """
    import sys
    call = str(args.get('call') or step.call)
    name, _, fn = call.partition('/')
    fn = fn or 'forward'
    kwargs = dict(args.get('args') or {})
    saved_path = list(sys.path)
    try:
        m = protocol()
        obj = m.mod(name)
        inst = obj() if isinstance(obj, type) else obj
        target = getattr(inst, fn, None)
        if target is None:
            have = ', '.join(sorted(f for f in dir(inst)
                                    if not f.startswith('_'))[:15])
            raise StepError(f'{name} has no fn {fn!r} — it has {have}', kind='fn')
        return target(**kwargs) if callable(target) else target
    except StepError as e:
        if e.kind not in ('import',):
            raise
        return _mod_over_http(name, fn, kwargs, step)
    except Exception as e:
        raise StepError(f'{call} raised {type(e).__name__}: {e}', kind='mod')
    finally:
        sys.path[:] = saved_path


def _mod_over_http(name, fn, kwargs, step):
    out, _ = request(f'{ACTIVATOR}/api/{name}/{fn}', 'POST', kwargs,
                     timeout=step.timeout or TIMEOUT)
    return out


# ── expr ─────────────────────────────────────────────────────────

def expr(step, args):
    """No call. Reshape what is already there.

    Between two tools there is nearly always a small transform — take the
    positions, drop the dust, sort by value, keep three. Without this a graph
    has to leave the fleet and come back, so it is declarative and here:
    value -> where -> sort_by/desc -> limit, and `pick` after that like any
    other step.
    """
    return args.get('value', step.value)


OPS = {
    '==': lambda a, b: a == b,
    '!=': lambda a, b: a != b,
    '>': lambda a, b: _num(a) > _num(b),
    '>=': lambda a, b: _num(a) >= _num(b),
    '<': lambda a, b: _num(a) < _num(b),
    '<=': lambda a, b: _num(a) <= _num(b),
    'in': lambda a, b: a in (b or []),
    'contains': lambda a, b: str(b).lower() in str(a).lower(),
    'exists': lambda a, b: a is not None,
    'missing': lambda a, b: a is None,
    'truthy': lambda a, b: bool(a),
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float('-inf')


def shape(value, step, over=None):
    """where / sort_by / desc / limit / pick, applied to any step's output.

    `over` carries the same fields with their ${...} already resolved — a
    `limit` that comes from an input is only a number once the run has one.
    """
    over = over or {}

    def field(name):
        v = over.get(name)
        return getattr(step, name) if v is None else v

    out = value
    where, sort_by, limit, pick = (field('where'), field('sort_by'),
                                   field('limit'), field('pick'))
    if where and isinstance(out, list):
        for clause in _clauses(where):
            path, op, want = clause
            fn = OPS.get(op)
            if not fn:
                raise StepError(f'{step.id}: unknown where operator {op!r} — '
                                f'{", ".join(OPS)}', kind='spec')
            out = [x for x in out if fn(_at(x, path), want)]
    if sort_by and isinstance(out, list):
        out = sorted(out, key=lambda x: _num(_at(x, sort_by)),
                     reverse=bool(step.desc))
    if limit is not None and isinstance(out, list):
        try:
            out = out[:int(limit)]
        except (TypeError, ValueError):
            raise StepError(f'{step.id}: limit must be a number, got {limit!r}',
                            kind='spec')
    if pick:
        out = refs.dig(out, pick, f'pick={pick}')
    return out


def _clauses(where):
    if isinstance(where, dict):
        return [(k, '==', v) for k, v in where.items()]
    if where and isinstance(where[0], str) and len(where) in (2, 3):
        where = [where]
    return [(c[0], c[1] if len(c) > 1 else 'truthy', c[2] if len(c) > 2 else None)
            for c in where]


def _at(obj, path):
    try:
        return refs.dig(obj, path)
    except refs.RefError:
        return None
