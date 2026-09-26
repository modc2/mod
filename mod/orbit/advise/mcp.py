#!/usr/bin/env python3
"""advise mcp — read a module you do not own, and recommend a change to it.

Thirteen tools in two groups. The read group (``advise_modules``,
``advise_brief``, ``advise_tree``, ``advise_file``, ``advise_grep``) is open
to anyone: it publishes a module's source with credentials redacted, which is
what an agent needs to form a specific opinion. The write group files that
opinion as a recommendation addressed to the module's owner — and stops. Only
``advise_approve``/``advise_reject`` move it, and only for the owner, proven
by ``token=`` (a mod-protocol token; stdio callers are the host and exempt).

Self-contained JSON-RPC 2.0 on the standard library, no ``mcp`` package.

    python3 mcp.py                      # stdio — one JSON message per line
    python3 mcp.py --http --port 50990  # Streamable HTTP — POST /mcp

serve.py mounts ``handle()`` at /mcp, and every tool dispatches into the same
``Mod`` class the REST routes use, so the surfaces cannot drift.
"""

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

# The anchor is loaded by path under its own name: importing it as `mod` would
# take the name the protocol package needs (see identity.py).
_spec = importlib.util.spec_from_file_location('advise_anchor',
                                               os.path.join(HERE, 'mod.py'))
_anchor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_anchor)

import recs as store                                          # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
DEFAULT_PROTOCOL_VERSION = '2025-03-26'

INSTRUCTIONS = (
    'Recommend changes to modules you do not own. Read first: advise_modules '
    'lists what is scannable, then advise_brief gives you one module whole — '
    'config, README, tree, languages, TODO/FIXME lines, the recommendations '
    'already filed (do not duplicate them) and the address that has to '
    'approve yours. advise_file and advise_grep get you line numbers; '
    'credentials are redacted and private modules are simply absent. '
    'Then advise_recommend files ONE specific proposed change with anchors '
    '(path + line), the reasoning, and optionally a patch. Filing runs '
    'nothing and needs no account — it waits for the module owner, who '
    'approves or rejects it, and whose own agent writes any code that '
    'results. Quality beats volume: a vague recommendation with no anchors '
    'is the one that gets rejected, and each author may hold only a few '
    'pending per module. advise_inbox/advise_approve/advise_reject are the '
    'owner side and need token= — a mod-protocol token for the address in '
    'that module\'s config.json.'
)

MOD_HTTP = _anchor.Mod(local=False)      # HTTP callers prove identity by token
MOD_LOCAL = _anchor.Mod(local=True)      # stdio is the host itself


def _str(desc, **extra):
    return {'type': 'string', 'description': desc, **extra}


def _num(desc):
    return {'type': 'number', 'description': desc}


_MODULE = _str('module name, as listed by advise_modules')
_TOKEN = _str('mod-protocol token proving the owner address; stdio callers '
              'are local and exempt')

TOOLS = {
    'advise_modules': {
        'description': 'Every module you may scan, with its description and '
                       'the address that owns it. Private modules are absent.',
        'inputSchema': {'type': 'object', 'properties': {
            'q': _str('filter by name or description'),
            'limit': _num('how many (default 400)')}},
        'handler': lambda m, a: m.modules(q=a.get('q'), limit=a.get('limit') or 400),
    },
    'advise_brief': {
        'description': 'One module, whole, in one call — config, README, file '
                       'tree, languages, biggest files, TODO/FIXME lines, the '
                       'recommendations already open on it, and who approves. '
                       'The right first call before recommending anything.',
        'inputSchema': {'type': 'object', 'properties': {
            'module': _MODULE,
            'focus': _str('also grep the module for this term'),
            'depth': _num('tree depth (default 3)')},
            'required': ['module']},
        'handler': lambda m, a: m.brief(module=a['module'], focus=a.get('focus'),
                                        depth=a.get('depth') or 3),
    },
    'advise_tree': {
        'description': 'A module\'s files — dependency and build directories '
                       'pruned, sizes included.',
        'inputSchema': {'type': 'object', 'properties': {
            'module': _MODULE, 'path': _str('subdirectory (default the root)'),
            'depth': _num('how deep (default 3, max 8)')},
            'required': ['module']},
        'handler': lambda m, a: m.tree(module=a['module'], path=a.get('path') or '',
                                       depth=a.get('depth') or 3),
    },
    'advise_file': {
        'description': 'Read one published file as text, with credential-shaped '
                       'values redacted. Bounded: pass start= to page through.',
        'inputSchema': {'type': 'object', 'properties': {
            'module': _MODULE, 'path': _str('path inside the module'),
            'start': _num('first line (default 1)'),
            'lines': _num('how many lines (default 600, max 600)')},
            'required': ['module', 'path']},
        'handler': lambda m, a: m.file(module=a['module'], path=a['path'],
                                       start=a.get('start') or 1,
                                       lines=a.get('lines') or 600),
    },
    'advise_grep': {
        'description': 'Literal search across a module with line numbers — how '
                       'you find the anchors a recommendation should cite.',
        'inputSchema': {'type': 'object', 'properties': {
            'module': _MODULE, 'query': _str('literal string to find'),
            'glob': _str('restrict to matching filenames, e.g. *.rs'),
            'limit': _num('max hits (default 80)')},
            'required': ['module', 'query']},
        'handler': lambda m, a: m.grep(module=a['module'], query=a['query'],
                                       glob=a.get('glob'), limit=a.get('limit') or 80),
    },
    'advise_recommend': {
        'description': 'File one proposed change to a module you do not own. '
                       'Runs nothing — it waits for that module\'s owner to '
                       'approve or reject. Be specific: one change, real '
                       'anchors (path + line), the reasoning behind it, and a '
                       'patch if you have one.',
        'inputSchema': {'type': 'object', 'properties': {
            'module': _MODULE,
            'title': _str('one line, what should change (<=200 chars)'),
            'summary': _str('two or three sentences the owner reads first'),
            'rationale': _str('why — the evidence from the code you read'),
            'change': _str('what to do, concretely enough to hand to an agent'),
            'anchors': {'type': 'array', 'description':
                        'where in the code: [{"path":"src/mod.py","line":88,'
                        '"note":"the retry loop"}]',
                        'items': {'type': 'object', 'properties': {
                            'path': _str('path inside the module'),
                            'line': _num('line number'),
                            'note': _str('what is at that line')},
                            'required': ['path']}},
            'patch': _str('optional unified diff — proposed, never applied'),
            'kind': _str('what sort of change', enum=list(store.KINDS)),
            'severity': _str('how much it matters', enum=list(store.SEVERITIES)),
            'confidence': _num('0..1 — how sure you are you read it right'),
            'effort': _str('your estimate, e.g. "20 minutes", "one file"'),
            'evidence': {'type': 'array', 'items': {'type': 'string'},
                         'description': 'quotes, logs or reproduction steps'},
            'agent': _str('which agent is recommending this, for attribution'),
            'token': _str('optional — sign it and it is filed under your '
                          'address instead of an anon handle')},
            'required': ['module', 'title']},
        'handler': lambda m, a: m.recommend(
            module=a['module'], title=a['title'], summary=a.get('summary', ''),
            rationale=a.get('rationale', ''), change=a.get('change', ''),
            anchors=a.get('anchors'), patch=a.get('patch', ''),
            kind=a.get('kind') or 'feature', severity=a.get('severity') or 'medium',
            confidence=a.get('confidence'), effort=a.get('effort', ''),
            evidence=a.get('evidence'), agent=a.get('agent', ''),
            token=a.get('token')),
    },
    'advise_recs': {
        'description': 'The queue — every recommendation, filterable by '
                       'module, status or author. Public.',
        'inputSchema': {'type': 'object', 'properties': {
            'module': _MODULE,
            'status': _str('filter by status', enum=list(store.STATUSES)),
            'author': _str('filter by author address or anon handle'),
            'limit': _num('how many (default 100)')}},
        'handler': lambda m, a: m.recs(module=a.get('module'), status=a.get('status'),
                                       author=a.get('author'),
                                       limit=a.get('limit') or 100),
    },
    'advise_rec': {
        'description': 'One recommendation whole — anchors, patch, thread, '
                       'decision and the suggestion an approval produced.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('recommendation id, rc_xxxxxxxx')}, 'required': ['id']},
        'handler': lambda m, a: m.rec(id=a['id']),
    },
    'advise_inbox': {
        'description': 'Recommendations waiting on YOUR signature, across '
                       'every module you own, worst severity first.',
        'inputSchema': {'type': 'object', 'properties': {
            'status': _str('default pending', enum=list(store.STATUSES)),
            'token': _TOKEN}},
        'handler': lambda m, a: m.inbox(status=a.get('status') or 'pending',
                                        token=a.get('token')),
    },
    'advise_outbox': {
        'description': 'What you have recommended to other owners, and how '
                       'much of it was accepted.',
        'inputSchema': {'type': 'object', 'properties': {
            'author': _str('address or anon handle (default: you)'),
            'token': _TOKEN}},
        'handler': lambda m, a: m.outbox(author=a.get('author'),
                                         token=a.get('token')),
    },
    'advise_approve': {
        'description': 'Accept a recommendation about a module you own. This '
                       'writes no code: it relays the recommendation into '
                       'build\'s idea queue as a suggestion for you to play '
                       'as an edit job, under your account, with your agent.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('recommendation id'),
            'note': _str('what you want kept or changed when it is played'),
            'token': _TOKEN}, 'required': ['id']},
        'handler': lambda m, a: m.approve(id=a['id'], note=a.get('note', ''),
                                          token=a.get('token')),
    },
    'advise_reject': {
        'description': 'Decline a recommendation about a module you own, with '
                       'a note saying why.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('recommendation id'), 'note': _str('why'),
            'token': _TOKEN}, 'required': ['id']},
        'handler': lambda m, a: m.reject(id=a['id'], note=a.get('note', ''),
                                         token=a.get('token')),
    },
    'advise_comment': {
        'description': 'Add to a recommendation\'s thread — an owner asking '
                       'one question is usually cheaper than a rejection.',
        'inputSchema': {'type': 'object', 'properties': {
            'id': _str('recommendation id'), 'body': _str('what to say'),
            'token': _str('optional — comment under your address')},
            'required': ['id', 'body']},
        'handler': lambda m, a: m.comment(id=a['id'], body=a['body'],
                                          token=a.get('token')),
    },
}


# ── JSON-RPC ─────────────────────────────────────────────────────────

def _result(id_, result):
    return {'jsonrpc': '2.0', 'id': id_, 'result': result}


def _error(id_, code, message):
    return {'jsonrpc': '2.0', 'id': id_, 'error': {'code': code, 'message': message}}


def call_tool(name, args, local=False):
    """Run one tool. Shared with the REST layer so a route and a tools/call
    cannot diverge. `local` is the transport's claim about the caller, never
    the caller's own — stdio is the host, HTTP has to bring a token."""
    tool = TOOLS.get(name)
    if not tool:
        raise LookupError(f'no tool named {name!r} — {", ".join(TOOLS)}')
    args = dict(args or {})
    for required in tool['inputSchema'].get('required', []):
        if args.get(required) in (None, ''):
            raise ValueError(f'{name} needs {required}')
    return tool['handler'](MOD_LOCAL if local else MOD_HTTP, args)


def _call(id_, params, local=False):
    name = (params or {}).get('name')
    args = (params or {}).get('arguments') or {}
    try:
        out = call_tool(name, args, local=local)
        return _result(id_, {
            'content': [{'type': 'text',
                         'text': json.dumps(out, default=str, indent=2)}],
            'structuredContent': out if isinstance(out, dict) else None,
            'isError': False})
    except Exception as e:
        return _result(id_, {'content': [{'type': 'text',
                                          'text': f'{type(e).__name__}: {e}'}],
                             'isError': True})


def handle(body, local=False):
    """One JSON-RPC message in, one response out (None for notifications)."""
    if not isinstance(body, dict) or not isinstance(body.get('method'), str):
        id_ = body.get('id') if isinstance(body, dict) else None
        return _error(id_, -32600, 'invalid request: expected a JSON-RPC 2.0 object')
    method, id_, params = body['method'], body.get('id'), body.get('params') or {}
    if id_ is None or method.startswith('notifications/'):
        return None
    if method == 'initialize':
        v = str(params.get('protocolVersion') or '')
        return _result(id_, {
            'protocolVersion': v if v in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION,
            'capabilities': {'tools': {}},
            'serverInfo': {'name': 'advise', 'version': version()},
            'instructions': INSTRUCTIONS})
    if method == 'ping':
        return _result(id_, {})
    if method == 'tools/list':
        return _result(id_, {'tools': tool_list()})
    if method == 'tools/call':
        return _call(id_, params, local=local)
    return _error(id_, -32601, f'method not found: {method}')


def version():
    try:
        with open(os.path.join(HERE, 'config.json')) as f:
            return json.load(f).get('version') or '0.0.0'
    except Exception:
        return '0.0.0'


def tool_list():
    return [{'name': n, 'description': t['description'], 'inputSchema': t['inputSchema']}
            for n, t in TOOLS.items()]


def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            body = json.loads(line)
        except Exception:
            resp = _error(None, -32700, 'parse error: line is not valid JSON')
        else:
            resp = handle(body, local=True)      # stdio is the host itself
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + '\n')
            sys.stdout.flush()


if __name__ == '__main__':
    argv = sys.argv[1:]
    if '--http' in argv:
        import serve as srv
        i = argv.index('--port') + 1 if '--port' in argv else -1
        srv.serve(int(argv[i]) if i > 0 else int(os.environ.get('PORT', 50990)))
    else:
        serve_stdio()
