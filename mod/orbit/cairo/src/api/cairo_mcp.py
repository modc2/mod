"""cairo_mcp.py — cairo's MCP server (JSON-RPC 2.0 over streamable HTTP).

    POST /cairo/mcp        {"jsonrpc":"2.0","id":1,"method":"tools/list"}
    GET  /cairo/mcp        a short card (name, tools) for humans and probes

Add it to Claude Code with:
    claude mcp add --transport http cairo http://localhost:51291/cairo/mcp

Every tool is a thin wrapper over one src/mod.py fn, so the MCP surface, the
REST API (/cairo/api/<fn>) and the console can never disagree. Access is the
REST rule, unchanged: reads + sandbox calls are open; tools marked owner
need a loopback caller or an owner token (header `token:` or Bearer), and
`deploy` is open only for a built contract that isn't live yet. The helper
agent (helper.py) calls these same tools in-process, so its trace reads as
MCP calls.
"""
import json

PROTOCOL = '2025-06-18'
SERVER_INFO = {'name': 'cairo', 'version': '0.2.0',
               'title': 'Cairo · ZK contract workbench + stablecoin school'}

S = lambda **p: {'type': 'object', 'properties': p,  # noqa: E731
                 'required': [k for k, v in p.items() if v.pop('_req', False)]}
STR = lambda d, req=False: {'type': 'string', 'description': d, '_req': req}  # noqa: E731
OBJ = lambda d: {'type': 'object', 'description': d}  # noqa: E731
INT = lambda d: {'type': 'integer', 'description': d}  # noqa: E731

# name -> (mod fn, description, input schema, owner-only, kwargs mapper)
TOOLS = {
    'cairo_tutorials': ('tutorials', 'The beginner lessons (stablecoins, smart contracts, tests) + a glossary. '
                        'Start here when a user is new.', S(), False, None),
    'cairo_lesson': ('tutorials', 'One full lesson by id (sections, code, the starter it builds).',
                     S(id=STR('lesson id from cairo_tutorials', True)), False, None),
    'cairo_templates': ('templates', 'Starter contracts a new contract can be made from '
                        '(counter, token, stablecoin, vault_stable, ...).', S(), False, None),
    'cairo_contracts': ('market', 'Every contract here: build state, reads/writes/events, '
                        'whether it is deployed on the practice chain.', S(), False, None),
    'cairo_contract': ('get', 'One contract: Scarb.toml, file list, build state.',
                       S(name=STR('contract name', True)), False, None),
    'cairo_read': ('read', 'Read a file of a contract (e.g. src/lib.cairo, tests/test_<name>.cairo, app.json).',
                   S(name=STR('contract name', True), path=STR('relative path', True)), False, None),
    'cairo_abi': ('abi', 'Built ABI: entrypoints (view/external), constructor, events.',
                  S(name=STR('contract name', True)), False, None),
    'cairo_app': ('app', 'Everything the contract\'s app sees: ABI, app.json labels, deployment, '
                  'the four practice accounts (alice/bob/carol/dave).',
                  S(name=STR('contract name', True)), False, None),
    'cairo_call': ('call', 'Call an entrypoint on the practice chain. Views return decoded values; '
                   'externals send a tx as `account` and return decoded events. args = {arg: value}; '
                   'amounts are raw units (18 decimals for coins), addresses may be account names.',
                   S(name=STR('contract name', True), fn=STR('entrypoint', True),
                     args=OBJ('arguments by name'), account=STR('alice|bob|carol|dave')), False, None),
    'cairo_events': ('events', 'Decoded events of the deployed contract, newest first.',
                     S(name=STR('contract name', True), limit=INT('max rows')), False, None),
    'cairo_deploy': ('deploy', 'Start (deploy) a built contract on the practice chain. args = constructor args by name.',
                     S(name=STR('contract name', True), args=OBJ('constructor args by name'),
                       account=STR('deployer: alice|bob|carol|dave')), False, None),
    'cairo_fleet': ('fleet', 'Every mod on this box with a Cairo setup (Scarb projects, starknet contracts, '
                    'or a config that mentions cairo/starknet), strongest evidence first.',
                    S(refresh={'type': 'boolean', 'description': 'skip the 60s cache'}), False, None),
    'cairo_whitepaper': ('whitepaper', 'The design paper of this workbench, as markdown.', S(), False, None),
    'cairo_jobs': (None, 'Recent build/test/vibe jobs with results (why did my test fail?).', S(), False, None),
    'cairo_new': ('new', 'OWNER. Make a new contract from a starter template.',
                  S(name=STR('snake_case name', True), template=STR('template id'), desc=STR('one-line description')),
                  True, None),
    'cairo_write': ('write', 'OWNER. Write a file inside a contract.',
                    S(name=STR('contract', True), path=STR('relative path', True), content=STR('file body', True)),
                    True, None),
    'cairo_build': ('build', 'OWNER. scarb build a contract; returns diagnostics with file:line.',
                    S(name=STR('contract name', True)), True, None),
    'cairo_test': ('test', 'OWNER. snforge test a contract (~30-60s); per-test pass/fail, gas and reasons.',
                   S(name=STR('contract name', True)), True, None),
    'cairo_fork': ('fork', 'OWNER. Copy a contract into a new one (package, module, dispatchers renamed).',
                   S(src=STR('contract to copy', True), name=STR('new snake_case name', True),
                     desc=STR('one-line description')), True, None),
    'cairo_vibe': ('vibe', 'OWNER. Hand a contract to the coding agent (orbit/claudecode) with a plain-English '
                   'ask; it edits the Cairo + tests, then builds and tests. Starts a job: poll cairo_job '
                   '(minutes). To make AND shape a new contract in one go, POST /cairo/agents/builder/run.',
                   S(name=STR('contract name (must exist; cairo_new/cairo_fork first)', True),
                     prompt=STR('what to change', True), model=STR('model for the agent')), True, None),
    'cairo_job': ('job', 'One job by id: state, live agent steps, result.',
                  S(id=STR('job id', True)), False, None),
}
# set by server.py: start(fn, kw) -> job, get(id) -> job (the REST job table)
HOOKS = {'start': None, 'get': None}


def tools_list():
    return [{'name': n, 'description': t[1], 'inputSchema': t[2],
             'annotations': {'readOnlyHint': t[0] in (None, 'tutorials', 'templates', 'market', 'get',
                                                      'read', 'abi', 'app', 'events', 'fleet',
                                                      'whitepaper'),
                             'title': n.replace('cairo_', '').replace('_', ' ')},
             '_meta': {'access': 'owner' if t[3] else 'open'}}
            for n, t in TOOLS.items()]


def call_tool(mod, name, args, gate, jobs=None):
    """Run one tool. gate(fn, kw) -> None | refusal text. Returns a dict."""
    if name not in TOOLS:
        raise KeyError(f'unknown tool {name!r}')
    fn, _, _, owner, _ = TOOLS[name]
    kw = dict(args or {})
    kw.pop('on_step', None)
    if fn == 'job':
        return HOOKS['get'](kw.get('id', '')) if HOOKS['get'] else {'error': 'no job table'}
    if fn is None:
        return {'jobs': [{k: j.get(k) for k in ('id', 'fn', 'name', 'state', 'error', 'result')}
                         for j in (jobs() if jobs else [])[:8]]}
    no = gate(fn, kw, owner)
    if no:
        return {'error': no, 'owner_only': True}
    if fn == 'vibe' and HOOKS['start']:
        return {'job': HOOKS['start']('vibe', kw)}
    return getattr(mod, fn)(**kw)


def handle(msg, mod, gate, jobs=None):
    """One JSON-RPC message -> response dict, or None for a notification."""
    mid, method = msg.get('id'), msg.get('method', '')
    p = msg.get('params') or {}

    def ok(result):
        return {'jsonrpc': '2.0', 'id': mid, 'result': result}

    def err(code, text):
        return {'jsonrpc': '2.0', 'id': mid, 'error': {'code': code, 'message': text}}

    if mid is None and method.startswith('notifications/'):
        return None
    if method == 'initialize':
        return ok({'protocolVersion': p.get('protocolVersion') or PROTOCOL,
                   'capabilities': {'tools': {'listChanged': False}},
                   'serverInfo': SERVER_INFO,
                   'instructions': 'Cairo/Starknet contract workbench and a beginner school for stablecoins. '
                                   'New user? cairo_tutorials, then cairo_lesson. To see a contract: cairo_contracts, '
                                   'cairo_app (ABI + plain-English labels + deployment), cairo_read for code. To use one: '
                                   'cairo_deploy then cairo_call on the practice chain (no real money; accounts '
                                   'alice/bob/carol/dave). Explain in plain words, with analogies, no jargon unexplained.'})
    if method == 'ping':
        return ok({})
    if method == 'tools/list':
        return ok({'tools': tools_list()})
    if method == 'tools/call':
        try:
            r = call_tool(mod, p.get('name'), p.get('arguments'), gate, jobs)
        except KeyError as e:
            return err(-32602, str(e).strip("'"))
        except Exception as e:  # noqa: BLE001  (bad kwargs, missing contract)
            return ok({'content': [{'type': 'text', 'text': f'error: {e}'}], 'isError': True})
        bad = isinstance(r, dict) and bool(r.get('error'))
        return ok({'content': [{'type': 'text', 'text': json.dumps(r, default=str)[:60000]}],
                   'structuredContent': r if isinstance(r, dict) else {'result': r},
                   'isError': bad})
    if method in ('resources/list', 'prompts/list'):
        return ok({method.split('/')[0]: []})
    return err(-32601, f'method not found: {method}')


def card():
    return {'name': 'cairo', 'transport': 'streamable-http', 'protocol': PROTOCOL,
            'add': 'claude mcp add --transport http cairo http://localhost:51291/cairo/mcp',
            'tools': [{'name': t['name'], 'access': t['_meta']['access'], 'description': t['description']}
                      for t in tools_list()]}
