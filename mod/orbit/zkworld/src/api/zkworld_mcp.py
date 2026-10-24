"""zkworld_mcp.py — zkworld's MCP server (JSON-RPC 2.0 over streamable HTTP).

    POST /zkworld/mcp        {"jsonrpc":"2.0","id":1,"method":"tools/list"}
    GET  /zkworld/mcp        a short card (name, tools) for humans and probes

Add it to Claude Code with:
    claude mcp add --transport http zkworld http://localhost:51350/zkworld/mcp

Every tool is a thin wrapper over one src/mod.py fn, so the MCP surface, the
REST API (/zkworld/api/<fn>) and the console can never disagree. Access is the
REST rule, unchanged: reads + sandbox calls are open; tools marked owner
need a loopback caller or an owner token (header `token:` or Bearer), and
`deploy` is open only for a built contract that isn't live yet. The helper
agent (helper.py) calls these same tools in-process, so its trace reads as
MCP calls.
"""
import json

PROTOCOL = '2025-06-18'
SERVER_INFO = {'name': 'zkworld', 'version': '0.2.0',
               'title': 'Cairo · ZK contract workbench + stablecoin school'}

S = lambda **p: {'type': 'object', 'properties': p,  # noqa: E731
                 'required': [k for k, v in p.items() if v.pop('_req', False)]}
STR = lambda d, req=False: {'type': 'string', 'description': d, '_req': req}  # noqa: E731
OBJ = lambda d: {'type': 'object', 'description': d}  # noqa: E731
INT = lambda d: {'type': 'integer', 'description': d}  # noqa: E731

# name -> (mod fn, description, input schema, owner-only, kwargs mapper)
TOOLS = {
    'zkworld_tutorials': ('tutorials', 'The beginner lessons (stablecoins, smart contracts, tests) + a glossary. '
                        'Start here when a user is new.', S(), False, None),
    'zkworld_lesson': ('tutorials', 'One full lesson by id (sections, code, the starter it builds).',
                     S(id=STR('lesson id from zkworld_tutorials', True)), False, None),
    'zkworld_frameworks': ('frameworks', 'Every ZK framework this workbench knows. Driven by an adapter: cairo, '
                         'circom, noir, zokrates, leo, gnark, halo2; the rest of the catalog (risc0, sp1, jolt, '
                         'arkworks, o1js, plonky3, ...) joins through the runner protocol (custom). Tools '
                         'installed, starters, which get a live chain app.',
                         S(id=STR('one framework id, with tool versions')), False, None),
    'zkworld_templates': ('templates', 'Starter projects a new one can be made from, every framework '
                        '(cairo: counter, token, stablecoin, ...; circom: multiplier, age_check; noir: square).',
                        S(framework=STR('cairo|circom|noir|zokrates|leo|gnark|halo2')), False, None),
    'zkworld_contracts': ('market', 'Every project here, any framework: build state, reads/writes/events '
                        '(cairo) or inputs/outputs/constraints (circuits), whether it is deployed.', S(), False, None),
    'zkworld_contract': ('get', 'One project: framework, manifest, file list, build state.',
                       S(name=STR('contract name', True)), False, None),
    'zkworld_read': ('read', 'Read a file of a contract (e.g. src/lib.cairo, tests/test_<name>.cairo, app.json).',
                   S(name=STR('contract name', True), path=STR('relative path', True)), False, None),
    'zkworld_abi': ('abi', 'Built shape: cairo entrypoints/constructor/events, or circuit inputs '
                  '(public/private), outputs and constraint count.',
                  S(name=STR('contract name', True)), False, None),
    'zkworld_app': ('app', 'Everything the contract\'s app sees: ABI, app.json labels, deployment, '
                  'the four practice accounts (alice/bob/carol/dave).',
                  S(name=STR('contract name', True)), False, None),
    'zkworld_call': ('call', 'Call an entrypoint on the practice chain. Views return decoded values; '
                   'externals send a tx as `account` and return decoded events. args = {arg: value}; '
                   'amounts are raw units (18 decimals for coins), addresses may be account names.',
                   S(name=STR('contract name', True), fn=STR('entrypoint', True),
                     args=OBJ('arguments by name'), account=STR('alice|bob|carol|dave')), False, None),
    'zkworld_events': ('events', 'Decoded events of the deployed contract, newest first.',
                     S(name=STR('contract name', True), limit=INT('max rows')), False, None),
    'zkworld_deploy': ('deploy', 'Start (deploy) a built contract on the practice chain. args = constructor args by name.',
                     S(name=STR('contract name', True), args=OBJ('constructor args by name'),
                       account=STR('deployer: alice|bob|carol|dave')), False, None),
    'zkworld_witness': ('witness', 'Run a project (any framework but cairo) on inputs {name: value}: '
                      'accepted + outputs, or which constraint rejected them. Computes only, no proof.',
                      S(name=STR('project name', True), input=OBJ('input signals by name')), False, None),
    'zkworld_statements': ('statements', 'Statements (what is proven) and the projects implementing each, one '
                         'per framework: e.g. factors in circom, noir, zokrates, leo, gnark, halo2.', S(), False, None),
    'zkworld_compare': ('compare', 'Run ONE input through every port of a statement (or listed projects): '
                      'holds or not in each framework, outputs, ms, and whether they agree.',
                      S(statement=STR('statement id (see zkworld_statements)'), names=STR('comma-separated projects'),
                        input=OBJ('inputs by name, e.g. {a, b, c}')), False, None),
    'zkworld_port': ('port_to', 'OWNER. Re-implement a project in another framework: new target project, the '
                   'source tests/*.json copied as the spec, then the agent rewrites it until every case '
                   'passes. Starts a job (minutes); poll zkworld_job.',
                   S(src=STR('project to port', True), framework=STR('target framework id', True),
                     name=STR('new project name (default <src>_<framework>)'), model=STR('agent model')),
                   True, None),
    'zkworld_fleet': ('fleet', 'Every mod on this box with a ZK setup in any framework (Scarb/Nargo projects, '
                    '*.circom, or a config that mentions a ZK toolchain), strongest evidence first.',
                    S(refresh={'type': 'boolean', 'description': 'skip the 60s cache'}), False, None),
    'zkworld_submods': ('submods', 'The framework submods: each framework (cairo, circom, noir, zokrates, leo, '
                      'gnark, halo2, custom) is a running mod zkworld.<id> with its own port and page at '
                      '/zkworld/<id>/, owning its projects. Port, running, project names per framework.',
                      S(), False, None),
    'zkworld_whitepaper': ('whitepaper', 'The design paper of this workbench, as markdown.', S(), False, None),
    'zkworld_jobs': (None, 'Recent build/test/vibe jobs with results (why did my test fail?).', S(), False, None),
    'zkworld_new': ('new', 'OWNER. Make a new project from a starter (framework inferred from the template).',
                  S(name=STR('snake_case name', True), template=STR('template id'),
                    framework=STR('any id from zkworld_frameworks (catalog ones scaffold a runner-protocol project)'),
                    desc=STR('one-line description')),
                  True, None),
    'zkworld_write': ('write', 'OWNER. Write a file inside a contract.',
                    S(name=STR('contract', True), path=STR('relative path', True), content=STR('file body', True)),
                    True, None),
    'zkworld_build': ('build', 'OWNER. Build a project (scarb / circom / nargo); returns diagnostics with file:line.',
                    S(name=STR('contract name', True)), True, None),
    'zkworld_test': ('test', 'OWNER. Test a project (snforge ~30-60s; circom JSON cases; nargo test); per-test pass/fail and reasons.',
                   S(name=STR('contract name', True)), True, None),
    'zkworld_fork': ('fork', 'OWNER. Copy a contract into a new one (package, module, dispatchers renamed).',
                   S(src=STR('contract to copy', True), name=STR('new snake_case name', True),
                     desc=STR('one-line description')), True, None),
    'zkworld_vibe': ('vibe', 'OWNER. Hand a contract to the coding agent (orbit/claudecode) with a plain-English '
                   'ask; it edits the Cairo + tests, then builds and tests. Starts a job: poll zkworld_job '
                   '(minutes). To make AND shape a new contract in one go, POST /zkworld/agents/builder/run.',
                   S(name=STR('contract name (must exist; zkworld_new/zkworld_fork first)', True),
                     prompt=STR('what to change', True), model=STR('model for the agent')), True, None),
    'zkworld_job': ('job', 'One job by id: state, live agent steps, result.',
                  S(id=STR('job id', True)), False, None),
}
# set by server.py: start(fn, kw) -> job, get(id) -> job (the REST job table)
HOOKS = {'start': None, 'get': None}


def tools_list():
    return [{'name': n, 'description': t[1], 'inputSchema': t[2],
             'annotations': {'readOnlyHint': t[0] in (None, 'tutorials', 'templates', 'market', 'get',
                                                      'read', 'abi', 'app', 'events', 'fleet',
                                                      'whitepaper', 'frameworks', 'witness',
                                                      'statements', 'compare'),
                             'title': n.replace('zkworld_', '').replace('_', ' ')},
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
    if fn in ('vibe', 'port_to') and HOOKS['start']:
        return {'job': HOOKS['start'](fn, kw)}
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
                                   'New user? zkworld_tutorials, then zkworld_lesson. To see a contract: zkworld_contracts, '
                                   'zkworld_app (ABI + plain-English labels + deployment), zkworld_read for code. To use one: '
                                   'zkworld_deploy then zkworld_call on the practice chain (no real money; accounts '
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
    return {'name': 'zkworld', 'transport': 'streamable-http', 'protocol': PROTOCOL,
            'add': 'claude mcp add --transport http zkworld http://localhost:51350/zkworld/mcp',
            'tools': [{'name': t['name'], 'access': t['_meta']['access'], 'description': t['description']}
                      for t in tools_list()]}
