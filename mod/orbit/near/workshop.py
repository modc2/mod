"""workshop.py — the LEARN + BUILD surface wired into mcp.py and api.py.

One place declares the workshop's MCP tools (near_lessons … near_ship), the
REST routes that mirror them, and the agent-contract + page routes
(/near/agents, /near/run[/stream], /near/workshop). mcp.py merges TOOLS and
api.py calls handle() first; neither has to know what is in here.

Access: reads are open. Writes (new/write/build/verify/check/vibe/ship/remove)
and the builder agent take the module's write token (~/.mod/near/token) OR a
loopback caller with no proxy headers (the host itself). Unlike the chain
write tools, which always want the token over HTTP, the workshop is a local
toolchain: cargo on this box, files in this module. ship() still goes through
wallet.deploy and keeps every testnet/confirm/token rule of its own.
"""
import json
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.append(HERE)

from chain import NearError   # noqa: E402

S = lambda **p: {'type': 'object', 'properties': p,  # noqa: E731
                 'required': [k for k, v in p.items() if v.pop('_req', False)]}
STR = lambda d, req=False: {'type': 'string', 'description': d, '_req': req}  # noqa: E731
BOOL = lambda d: {'type': 'boolean', 'description': d}  # noqa: E731
OBJ = lambda d: {'type': ['object', 'string'], 'description': d}  # noqa: E731
INT = lambda d: {'type': 'integer', 'description': d}  # noqa: E731


def _studio():
    import studio
    return studio


def _lessons():
    import lessons
    return lessons


def _lesson(a):
    l = _lessons().lesson(str(a.get('id') or ''))
    if not l:
        raise NearError(f'no lesson {a.get("id")!r} — ' + ', '.join(x['id'] for x in _lessons().LESSONS),
                        status=404)
    return l


def _zk(a):
    """The cairo mod's school and contracts, read over its MCP: the ZK half
    of the ladder lives there."""
    import tutor
    what = str(a.get('what') or 'lessons')
    if what == 'lesson':
        return tutor.cairo_mcp('cairo_lesson', {'id': a.get('id')})
    if what == 'contracts':
        return tutor.cairo_mcp('cairo_contracts')
    if what == 'templates':
        return tutor.cairo_mcp('cairo_templates')
    out = tutor.cairo_mcp('cairo_tutorials')
    out['cairo'] = tutor.CAIRO
    out['workbench'] = '/cairo/'
    return out


def _ask(a):
    import tutor
    ev = tutor.run({'query': a.get('query'), 'brain': a.get('brain') or 'tutor',
                    'context': a.get('context') or {}},
                   who={'writable': bool(a.get('_local')), 'why': 'MCP caller'},
                   emit=lambda e: None, start_job=lambda *x: None, get_job=lambda *x: None,
                   agent='tutor')
    return ev


def _job_start(fn):
    def h(a):
        kw = {'name': a['name']}
        if fn == 'vibe':
            kw['prompt'] = str(a.get('prompt') or '')
            if a.get('model'):
                kw['model'] = a['model']
        if a.get('wait') or a.get('sync'):
            return getattr(_studio(), fn)(**kw)
        return _studio().jobs().start(fn, kw)
    return h


def _vibe(a):
    if not str(a.get('prompt') or '').strip():
        raise NearError('vibe needs prompt= — what to change')
    return _job_start('vibe')(a)


def _ship(a):
    init_args = a.get('init_args')
    if isinstance(init_args, str) and init_args.strip():
        init_args = json.loads(init_args)
    return _studio().ship(a['name'], a.get('account_id'), a.get('network') or 'testnet',
                          a.get('init_method'), init_args, bool(a.get('confirm')), a.get('rpc'))


READ = {
    'near_lessons': ('The workshop school: every lesson (id, title, level, minutes, summary) and the '
                     'glossary. Start here when someone is new to NEAR contracts or zero knowledge.',
                     S(), lambda a: _lessons().index()),
    'near_lesson': ('One full lesson: sections with plain-English text, code, tables, tips, and the '
                    'starter it builds.', S(id=STR('lesson id from near_lessons', True)), _lesson),
    'near_templates': ('The starters a new project can be made from (counter, token, commit_reveal, '
                       'merkle_allowlist), each with its walkthrough notes and steps.',
                       S(), lambda a: _studio().templates()),
    'near_projects': ('Every project in the workshop: starter, build state (unbuilt/built/stale), '
                      '.wasm size, where it has been shipped.', S(), lambda a: _studio().projects()),
    'near_project': ('One project: files, methods parsed from lib.rs (view/change/init, payable), the '
                     'walkthrough pinned to line numbers, build artifact, steps to try it.',
                     S(name=STR('project name', True)), lambda a: _studio().project(a['name'])),
    'near_read': ('Read a file of a project (src/lib.rs, Cargo.toml, README.md).',
                  S(name=STR('project name', True), path=STR('relative path', True)),
                  lambda a: _studio().read(a['name'], a['path'])),
    'near_jobs': ('Recent build / verify / check / vibe jobs (state, seconds, steps), newest first.',
                  S(name=STR('only this project'), limit=INT('max rows')),
                  lambda a: _studio().jobs().list(a.get('name'), a.get('limit') or 30)),
    'near_job': ('One job with its full result (build log + diagnostics, tests, vibe steps).',
                 S(id=STR('job id', True)), lambda a: _studio().jobs().get(a['id'])),
    'near_toolchain': ('Is cargo + the wasm32 target here, and is the claudecode agent reachable.',
                       S(), lambda a: _studio().toolchain()),
    'near_merkle': ('Sorted-pair sha256 merkle tree over account ids — the exact scheme the '
                    'merkle_allowlist starter verifies. Returns the root and each account\'s proof.',
                    S(accounts=OBJ('account ids: a list, or comma/space separated', )),
                    lambda a: _studio().merkle(a.get('accounts') or '')),
    'near_seal': ('The commit_reveal starter\'s seal: sha256("contract|who|value|salt"), hex.',
                  S(contract=STR('the contract account', True), who=STR('the committing account', True),
                    value=INT('the number being sealed'), salt=STR('any secret word', True)),
                  lambda a: _studio().seal(a['contract'], a['who'], a.get('value') or 0, a['salt'])),
    'near_zk': ('The zero-knowledge half of the ladder, read from the cairo mod over its MCP: '
                'what=lessons (default) | lesson (+id) | contracts | templates. Says so if the '
                'cairo mod is down.', S(what=STR('lessons | lesson | contracts | templates'),
                                       id=STR('cairo lesson id, with what=lesson')), _zk),
    'near_ask': ('Ask the tutor (free, no model): explains NEAR contracts and the ZK ladder, reads '
                 'your projects and their last build/test, any contract on chain, and the cairo '
                 'mod\'s lessons. Returns {answer, actions, result: steps}.',
                 S(query=STR('the question', True), context=OBJ('{project, lesson, account, network}')),
                 _ask),
}

WRITE = {
    'near_new': ('Scaffold a project from a starter (a Cargo crate under contracts/<name>/).',
                 S(name=STR('project name (snake_case)', True),
                   template=STR('counter | token | commit_reveal | merkle_allowlist'),
                   desc=STR('one line about it')),
                 lambda a: _studio().new(a['name'], a.get('template') or 'counter', a.get('desc'))),
    'near_write': ('Write a file in a project (src/lib.rs, Cargo.toml, README.md).',
                   S(name=STR('project name', True), path=STR('relative path', True),
                     content=STR('the whole file', True)),
                   lambda a: _studio().write(a['name'], a['path'], a['content'])),
    'near_build': ('cargo build → .wasm (a job; poll near_job; wait=true blocks). Returns the log, '
                   'diagnostics pinned to file:line, and the artifact with its exported methods.',
                   S(name=STR('project name', True), wait=BOOL('block until done')), _job_start('build')),
    'near_verify': ('cargo test — the unit tests in lib.rs (a job; wait=true blocks). Per-test '
                    'pass/fail with the panic reason.',
                    S(name=STR('project name', True), wait=BOOL('block until done')), _job_start('verify')),
    'near_check': ('build + verify in one job.',
                   S(name=STR('project name', True), wait=BOOL('block until done')), _job_start('check')),
    'near_vibe': ('Hand the project to orbit/claudecode with an ask ("add a pause switch"), then build '
                  '+ test (a job; its steps stream into near_job). Bills the host Claude account.',
                  S(name=STR('project name', True), prompt=STR('what to change', True),
                    model=STR('model for claudecode'), wait=BOOL('block until done')), _vibe),
    'near_ship': ('Deploy the built .wasm with the keystore (wallet.deploy): testnet by default, '
                  'mainnet needs confirm=true. Batches the starter\'s init the first time; a redeploy '
                  'upgrades in place and skips it.',
                  S(name=STR('project name', True), account_id=STR('keystore account (default signer)'),
                    network=STR('testnet (default) | mainnet'), init_method=STR('override the init method'),
                    init_args=OBJ('override the init args'), confirm=BOOL('required off testnet')), _ship),
    'near_remove': ('Delete a project directory.', S(name=STR('project name', True)),
                    lambda a: _studio().remove(a['name'])),
}

TOOLS = {}
for _n, (_d, _s, _h) in {**READ, **WRITE}.items():
    TOOLS[_n] = {'description': _d, 'inputSchema': _s, 'handler': _h}

ROUTES = {
    '/lessons': 'near_lessons', '/lesson': 'near_lesson', '/templates': 'near_templates',
    '/projects': 'near_projects', '/project': 'near_project', '/read': 'near_read',
    '/jobs': 'near_jobs', '/job': 'near_job', '/toolchain': 'near_toolchain',
    '/merkle': 'near_merkle', '/seal': 'near_seal', '/zk': 'near_zk', '/ask': 'near_ask',
    '/new': 'near_new', '/write': 'near_write', '/build': 'near_build', '/verify': 'near_verify',
    '/check': 'near_check', '/vibe': 'near_vibe', '/ship': 'near_ship', '/remove': 'near_remove',
}


# ── the write gate ───────────────────────────────────────────────

def token_ok(token):
    """The module's write token, created on first use if no wallet made it yet."""
    import wallet
    held = wallet.read_token() or wallet.ensure_token()
    return bool(token) and secrets.compare_digest(str(token), held)


def gate(name, args, loopback=False):
    """Pops token= and raises unless the caller may write."""
    token = args.pop('token', None)
    if name in WRITE and not loopback and not token_ok(token):
        raise NearError('the workshop\'s writes need the token from ~/.mod/near/token '
                        '(pass token=, or call from the host itself)', status=403)
    if name == 'near_ask':
        args['_local'] = bool(loopback or token_ok(token))


def is_loopback(ip, headers):
    """The host itself: a loopback peer with no proxy headers. The gateway
    (caddy) always adds X-Forwarded-For, so a routed request never passes."""
    return ip in ('127.0.0.1', '::1', '::ffff:127.0.0.1') and \
        not headers.get('x-forwarded-for') and not headers.get('x-real-ip')


def whoami(ip, headers, body):
    token = (body or {}).get('token') or headers.get('token') or \
        (headers.get('authorization') or '').replace('Bearer ', '').strip()
    if is_loopback(ip, headers):
        return {'writable': True, 'why': 'loopback'}
    if token_ok(token):
        return {'writable': True, 'why': 'token'}
    return {'writable': False, 'why': 'no write token (WALLET tab → remote-write token)'}


# ── http: agents, run/stream, the page ───────────────────────────

def _special(p):
    segs = [s for s in p.split('/') if s]
    if segs[:1] == ['agents'] and segs[2:] in (['run'], ['run', 'stream']):
        return ('run', len(segs) == 4, segs[1])
    if segs[:1] == ['agents'] and len(segs) <= 2:
        return ('agents', segs[1] if len(segs) == 2 else None)
    if segs == ['.well-known', 'agent.json']:
        return ('card',)
    if segs in (['run'], ['run', 'stream']):
        return ('run', len(segs) == 2, None)
    if segs in (['workshop'], ['learn'], ['build']):
        return ('page',)
    if segs == ['whoami']:
        return ('whoami',)
    return None


def handle(h, p, query, base, read_body):
    """Serve the workshop's own routes on the api.py Handler `h`. Returns
    True when handled. Everything else falls through to route()."""
    sp = _special(p)
    if not sp:
        return False
    import tutor
    ip = h.client_address[0]
    if sp[0] == 'page':
        if h.command != 'GET':
            h._send(405, {'error': 'GET'})
            return True
        try:
            with open(os.path.join(HERE, 'workshop.html'), 'rb') as f:
                h._send(200, f.read(), 'text/html; charset=utf-8')
        except FileNotFoundError:
            h._send(404, {'error': 'workshop.html missing'})
        return True
    if sp[0] == 'card':
        h._send(200, tutor.card(base))
        return True
    if sp[0] == 'whoami':
        h._send(200, whoami(ip, h.headers, read_body() if h.command == 'POST' else {}))
        return True
    if sp[0] == 'agents':
        if sp[1]:
            a = tutor.AGENTS.get(sp[1])
            h._send(200 if a else 404, a or {'error': f'no agent {sp[1]}', 'agents': list(tutor.AGENTS)})
        else:
            h._send(200, {'agents': list(tutor.AGENTS.values()), 'examples': tutor.EXAMPLES,
                          'card': f'{base}/.well-known/agent.json'})
        return True
    # run / run/stream
    if h.command != 'POST':
        h._send(405, {'error': 'POST {query, agent?, brain?, context?}'})
        return True
    body = read_body()
    who = whoami(ip, h.headers, body)
    import studio
    jobs = studio.jobs()
    mcp_url = f'http://127.0.0.1:{os.environ.get("PORT", 50910)}/mcp'
    args = dict(body=body, who=who, start_job=jobs.start, get_job=jobs.get,
                agent=sp[2], mcp_url=mcp_url)
    if not sp[1]:
        ev = tutor.run(emit=lambda e: None, **args)
        h._send(200 if ev.get('type') != 'error' else 400, ev)
        return True
    h.send_response(200)
    h.send_header('Content-Type', 'text/event-stream')
    h.send_header('Cache-Control', 'no-cache')
    h.send_header('X-Accel-Buffering', 'no')
    h.send_header('Access-Control-Allow-Origin', '*')
    h.send_header('Connection', 'close')
    h.end_headers()

    def emit(ev):
        try:
            h.wfile.write(f'data: {json.dumps(ev, default=str)}\n\n'.encode())
            h.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
    try:
        tutor.run(emit=emit, **args)
    except Exception as e:  # noqa: BLE001
        emit({'type': 'error', 'error': f'{type(e).__name__}: {e}'})
    try:
        h.wfile.flush()
    except Exception:  # noqa: BLE001
        pass
    h.close_connection = True
    return True
