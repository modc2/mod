"""reviewer as an MCP server: one JSON-RPC dispatch behind both transports.

* stdio:  ``python3 mcp_server.py``   (cwd = this module dir)
* HTTP:   ``POST /mcp`` on serve.py, which imports handle_message()

    claude mcp add reviewer -- python3 /root/mod/mod/orbit/reviewer/mcp_server.py
    claude mcp add --transport http reviewer http://localhost:51430/mcp

Reads are open. reviewer_review with engine=agent spends on this box, so over
HTTP it answers loopback callers only (REVIEWER_OPEN_AGENT=1 lifts that).
"""

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

import mod as anchor  # noqa: E402

PROTOCOL_VERSION = '2025-06-18'
SUPPORTED = ('2025-06-18', '2025-03-26', '2024-11-05')
INSTRUCTIONS = (
    'Code review for the mod fleet. reviewer_targets lists what this box can review; '
    'reviewer_review runs one (a module name, a path, pasted code, or a unified diff) and '
    'returns scored findings at file:line with a fix each. engine=static is offline and '
    'instant; engine=agent adds a written review from the claudecode agent and costs money. '
    'reviewer_get / reviewer_list read past reviews; reviewer_rules explains every rule.')

_mod = None


def the_mod():
    global _mod
    if _mod is None:
        _mod = anchor.Mod()
    return _mod


def _version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version', '0')
    except Exception:  # noqa: BLE001
        return '0'


def _str(desc, **kw):
    return {'type': 'string', 'description': desc, **kw}


def _review(a, local):
    t = a.get('target') or a.get('path')
    if t and not local:
        p = the_mod().resolve(t)
        if p and not the_mod().in_fleet(p):
            raise PermissionError('remote callers can only review fleet modules')
    engine = (a.get('engine') or 'static').lower()
    if engine == 'agent' and not local and os.environ.get('REVIEWER_OPEN_AGENT') != '1':
        raise PermissionError('engine=agent spends on this box: loopback callers only')
    r = the_mod().review(target=a.get('target'), path=a.get('path'), code=a.get('code'),
                         filename=a.get('filename') or 'snippet.py', diff=a.get('diff'),
                         engine=engine, focus=a.get('focus', ''), model=a.get('model'),
                         timeout=int(a.get('timeout') or 600), save=bool(a.get('save', True)))
    # keep the tool result readable: cap the findings list
    cap = int(a.get('max_findings') or 80)
    if len(r['findings']) > cap:
        r['findings_truncated'] = len(r['findings']) - cap
        r['findings'] = r['findings'][:cap]
    return r


TOOLS = {
    'reviewer_review': {
        'fn': _review,
        'description': 'Review a module (by name), a path, pasted code, or a unified diff. Returns '
                       'score 0-100, grade, counts by severity, and findings at file:line with a fix. '
                       'engine=static is offline; engine=agent adds a claudecode written review (costs money).',
        'schema': {'type': 'object', 'properties': {
            'target': _str('Module name from reviewer_targets, or a path'),
            'path': _str('Absolute path to a directory or file'),
            'code': _str('Pasted source to review instead of a path'),
            'filename': _str('Name for pasted code; its extension picks the rules', default='snippet.py'),
            'diff': _str('A unified diff; only added lines are reviewed'),
            'engine': {'type': 'string', 'enum': ['static', 'agent'], 'default': 'static'},
            'focus': _str('What the agent engine should look hardest at'),
            'model': _str('Model for the agent engine (default: the agent\'s own)'),
            'timeout': {'type': 'integer', 'default': 600},
            'save': {'type': 'boolean', 'default': True},
            'max_findings': {'type': 'integer', 'default': 80},
        }},
        'read_only': False, 'destructive': False,
    },
    'reviewer_get': {
        'fn': lambda a, local: the_mod().get(a['id']),
        'description': 'One past review with every finding and its verdict.',
        'schema': {'type': 'object', 'required': ['id'], 'properties': {'id': _str('Review id')}},
        'read_only': True,
    },
    'reviewer_list': {
        'fn': lambda a, local: the_mod().list(a.get('target', ''), a.get('engine', ''),
                                               int(a.get('limit') or 30), int(a.get('offset') or 0)),
        'description': 'Past reviews, newest first (optionally for one target or engine).',
        'schema': {'type': 'object', 'properties': {
            'target': _str('Only this target'), 'engine': _str('static | agent'),
            'limit': {'type': 'integer', 'default': 30}, 'offset': {'type': 'integer', 'default': 0}}},
        'read_only': True,
    },
    'reviewer_verdict': {
        'fn': lambda a, local: the_mod().verdict(a['id'], int(a['index']), a.get('verdict', 'fixed'), a.get('note', '')),
        'description': 'Mark one finding open | fixed | ignored; the review score follows.',
        'schema': {'type': 'object', 'required': ['id', 'index'], 'properties': {
            'id': _str('Review id'), 'index': {'type': 'integer', 'description': 'Finding index'},
            'verdict': {'type': 'string', 'enum': ['open', 'fixed', 'ignored'], 'default': 'fixed'},
            'note': _str('Why')}},
        'read_only': False, 'destructive': False,
    },
    'reviewer_rules': {
        'fn': lambda a, local: the_mod().rules(),
        'description': 'Every rule the static engine runs: id, severity, what it looks for, the fix.',
        'schema': {'type': 'object', 'properties': {}},
        'read_only': True,
    },
    'reviewer_targets': {
        'fn': lambda a, local: the_mod().targets(a.get('q', '')),
        'description': 'Every module this box can review, with a search filter.',
        'schema': {'type': 'object', 'properties': {'q': _str('Filter by name or description')}},
        'read_only': True,
    },
    'reviewer_status': {
        'fn': lambda a, local: the_mod().health(),
        'description': 'Liveness, store stats, and whether the claudecode agent is reachable.',
        'schema': {'type': 'object', 'properties': {}},
        'read_only': True,
    },
}


def tool_list():
    out = []
    for name, t in TOOLS.items():
        out.append({'name': name, 'description': t['description'], 'inputSchema': t['schema'],
                    'annotations': {'readOnlyHint': t.get('read_only', False),
                                    'destructiveHint': t.get('destructive', False),
                                    'idempotentHint': t.get('read_only', False),
                                    'openWorldHint': False}})
    return out


def describe():
    return {'name': 'reviewer', 'version': _version(), 'protocol': PROTOCOL_VERSION,
            'instructions': INSTRUCTIONS, 'tools': [t['name'] for t in tool_list()],
            'transport': 'POST JSON-RPC here; or stdio: python3 mcp_server.py'}


def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message, data=None):
    e = {'code': code, 'message': message}
    if data is not None:
        e['data'] = data
    return {'jsonrpc': '2.0', 'id': id_, 'error': e}


def handle_message(msg, local=True):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if isinstance(msg, list):
        out = [handle_message(m, local) for m in msg]
        return [o for o in out if o is not None] or None
    if not isinstance(msg, dict):
        return _error(None, -32600, 'invalid request')
    id_ = msg.get('id')
    method = msg.get('method') or ''
    params = msg.get('params') or {}
    if method.startswith('notifications/'):
        return None
    if method == 'initialize':
        v = params.get('protocolVersion')
        return _result(id_, {'protocolVersion': v if v in SUPPORTED else PROTOCOL_VERSION,
                             'capabilities': {'tools': {'listChanged': False}},
                             'serverInfo': {'name': 'reviewer', 'title': 'Reviewer', 'version': _version()},
                             'instructions': INSTRUCTIONS})
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        name = params.get('name')
        t = TOOLS.get(name)
        if not t:
            return _error(id_, -32602, f'unknown tool {name}')
        args = params.get('arguments') or {}
        try:
            res = t['fn'](args, local)
            return _result(id_, {'content': [{'type': 'text', 'text': json.dumps(res, indent=1, default=str)}],
                                 'structuredContent': res if isinstance(res, dict) else {'result': res},
                                 'isError': False})
        except Exception as e:  # noqa: BLE001 — the tool result carries the reason
            return _result(id_, {'content': [{'type': 'text', 'text': f'{type(e).__name__}: {e}'}],
                                 'isError': True})
    if id_ is None:
        return None
    return _error(id_, -32601, f'method not found: {method}')


def main():
    """stdio transport: one JSON-RPC message per line."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps(_error(None, -32700, 'parse error')) + '\n')
            sys.stdout.flush()
            continue
        try:
            resp = handle_message(msg, local=True)
        except Exception as e:  # noqa: BLE001
            resp = _error(msg.get('id') if isinstance(msg, dict) else None, -32603,
                          str(e), traceback.format_exc()[-2000:])
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    main()
