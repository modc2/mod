"""studio.py — the NEAR contract workshop: starters, projects, build, test, vibe, ship.

A project is a Cargo crate under contracts/<name>/ (NEAR_CONTRACTS overrides),
made from a starter in templates/<id>/ (meta.json + lib.rs.tmpl, shared
_Cargo.toml.tmpl). The toolchain is the host's own cargo with the
wasm32-unknown-unknown target; every project shares one CARGO_TARGET_DIR so
near-sdk compiles once (~30s) and each project after that builds in seconds.

    new(name, template)   scaffold          build(name)   cargo -> .wasm
    verify(name)          cargo test        vibe(name, prompt)  claudecode edits
    ship(name, ...)       wallet.deploy()   merkle(accounts)    root + proofs

Builds, tests and vibes are long: Jobs runs them on a thread and keeps the
table in contracts/.jobs.json so the console, MCP and the builder agent see
the same rows. Nothing here signs anything; ship() hands the .wasm to
wallet.deploy, which keeps all the testnet/confirm/token rules.
"""

import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, 'templates')
ROOT = os.path.expanduser(os.environ.get('NEAR_CONTRACTS') or os.path.join(HERE, 'contracts'))
TARGET = os.path.join(ROOT, '.target')
CARGO_BIN = os.path.expanduser('~/.cargo/bin')
AGENT_URL = os.environ.get('NEAR_AGENT_URL', 'http://127.0.0.1:51260').rstrip('/')
WASM_TARGET = 'wasm32-unknown-unknown'
RESERVED = {'near', 'src', 'target', 'test', 'tests', 'templates', 'contracts', 'api',
            'mcp', 'agents', 'run', 'lessons', 'build', 'new'}
META_FILE = '.studio.json'

SYSTEM_PROMPT = (
    "You are a NEAR smart-contract engineer working inside ONE Cargo crate (the "
    "current working directory). Target near-sdk 5 (the `#[near]` macros), Rust "
    "2021, compiled with `cargo build --target wasm32-unknown-unknown --release`.\n"
    "Rules:\n"
    "- The contract is src/lib.rs: a `#[near(contract_state)]` struct (derive "
    "PanicOnDefault + an `#[init]` constructor, or Default), `#[near] impl` with "
    "`&self` views and `&mut self` changes. Public amounts use "
    "near_sdk::json_types::U128; collections come from near_sdk::store "
    "(LookupMap/IterableMap/IterableSet) with unique byte prefixes.\n"
    "- Who-is-calling is env::predecessor_account_id(); refuse with require!(). "
    "Events are env::log_str(\"EVENT_JSON:{...}\") (NEP-297).\n"
    "- Unit tests live in the same file under #[cfg(test)] and use "
    "near_sdk::test_utils::VMContextBuilder + testing_env!. Keep them meaningful: "
    "cover the rules you add, including the refusals (#[should_panic(expected=..)]).\n"
    "- Edit files directly, then ALWAYS run `cargo build --target "
    "wasm32-unknown-unknown --release` and `cargo test` and fix what you broke "
    "before you finish. Do not add dependencies beyond near-sdk unless asked.\n"
    "- Keep .studio.json's \"init\" in sync when you change the constructor "
    "(method name + example args), so the workshop can batch it into the deploy.\n"
    "- Comment WHY, not WHAT; the person reading may be learning Rust and NEAR."
)


# ── names ────────────────────────────────────────────────────────

def slug(name):
    """A crate name: snake_case, 2-32 chars, not reserved."""
    s = re.sub(r'[^a-z0-9_]+', '_', str(name or '').strip().lower().replace('-', '_')).strip('_')
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,31}', s or ''):
        raise ValueError(f'{name!r} is not a usable name — letters, digits and _, '
                         'starting with a letter, 2-32 chars')
    if s in RESERVED:
        raise ValueError(f'{s!r} is reserved')
    return s


def pascal(pkg):
    return ''.join(w.capitalize() for w in pkg.split('_') if w)


def _dir(name):
    pkg = slug(name)
    return pkg, os.path.join(ROOT, pkg)


def _is_project(d):
    return os.path.isfile(os.path.join(d, 'Cargo.toml')) and \
        os.path.isfile(os.path.join(d, 'src', 'lib.rs'))


# ── starters ─────────────────────────────────────────────────────

def templates():
    out = []
    for p in sorted(glob.glob(os.path.join(TEMPLATES, '*', 'meta.json'))):
        try:
            m = json.load(open(p))
        except Exception as e:  # noqa: BLE001
            m = {'id': os.path.basename(os.path.dirname(p)), 'error': str(e)}
        m['walkthrough_steps'] = len(m.get('walkthrough') or [])
        out.append(m)
    order = {'start here': 0, 'next': 1, 'zk': 2}
    out.sort(key=lambda m: (order.get(m.get('level'), 9), m.get('id')))
    return {'templates': out, 'count': len(out)}


def starter(tid):
    p = os.path.join(TEMPLATES, str(tid), 'meta.json')
    if not os.path.isfile(p):
        raise ValueError(f'no starter {tid!r} — ' + ', '.join(
            m['id'] for m in templates()['templates']))
    return json.load(open(p))


def _render(text, pkg, desc, tid):
    return (text.replace('__NAME__', pkg).replace('__TITLE__', pascal(pkg))
            .replace('__DESC__', desc.replace('"', "'")).replace('__TEMPLATE__', tid))


# ── projects ─────────────────────────────────────────────────────

def _meta_path(d):
    return os.path.join(d, META_FILE)


def _meta(d):
    try:
        return json.load(open(_meta_path(d)))
    except Exception:  # noqa: BLE001
        return {}


def _save_meta(d, m):
    with open(_meta_path(d), 'w') as f:
        json.dump(m, f, indent=2)


def new(name, template='counter', desc=None):
    """Scaffold a project from a starter. Refuses to overwrite."""
    pkg, d = _dir(name)
    tid = str(template or 'counter')
    meta = starter(tid)
    if os.path.exists(d):
        raise ValueError(f'{pkg} already exists')
    desc = str(desc or meta.get('blurb', '').split('.')[0] or pkg)
    os.makedirs(os.path.join(d, 'src'))
    tdir = os.path.join(TEMPLATES, tid)
    files = {
        'Cargo.toml': open(os.path.join(TEMPLATES, '_Cargo.toml.tmpl')).read(),
        'src/lib.rs': open(os.path.join(tdir, 'lib.rs.tmpl')).read(),
        'README.md': open(os.path.join(TEMPLATES, '_README.md.tmpl')).read(),
        '.gitignore': open(os.path.join(TEMPLATES, '_gitignore.tmpl')).read(),
    }
    for rel, text in files.items():
        with open(os.path.join(d, rel), 'w') as f:
            f.write(_render(text, pkg, desc, tid))
    _save_meta(d, {'name': pkg, 'template': tid, 'desc': desc,
                   'created': int(time.time()), 'init': meta.get('init'),
                   'cairo_twin': meta.get('cairo_twin'), 'deployed': []})
    return {'name': pkg, 'dir': d, 'template': tid, 'desc': desc,
            'files': sorted(files), 'init': meta.get('init')}


def remove(name):
    pkg, d = _dir(name)
    if not _is_project(d):
        raise ValueError(f'no project {pkg!r}')
    shutil.rmtree(d)
    return {'removed': pkg}


def _tree(d):
    out = []
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x not in ('target', '.target', '.git')]
        for f in files:
            if f.endswith(('.lock',)) or f == META_FILE:
                continue
            p = os.path.join(root, f)
            out.append({'path': os.path.relpath(p, d).replace(os.sep, '/'),
                        'bytes': os.path.getsize(p)})
    out.sort(key=lambda x: (not x['path'].startswith('src/'), x['path']))
    return out


def _src_mtime(d):
    ts = [os.path.getmtime(os.path.join(d, 'Cargo.toml'))]
    for p in glob.glob(os.path.join(d, 'src', '**', '*.rs'), recursive=True):
        ts.append(os.path.getmtime(p))
    return max(ts)


def wasm_path(pkg):
    return os.path.join(TARGET, WASM_TARGET, 'release', f'{pkg}.wasm')


def _wasm_info(pkg, d):
    p = wasm_path(pkg)
    if not os.path.isfile(p):
        return None, 'unbuilt'
    code = open(p, 'rb').read()
    try:
        from chain import _wasm_exports
        exports = _wasm_exports(code)
    except Exception:  # noqa: BLE001
        exports = []
    fresh = os.path.getmtime(p) >= _src_mtime(d)
    return ({'path': p, 'bytes': len(code), 'sha256': hashlib.sha256(code).hexdigest(),
             'exports': exports, 'built': int(os.path.getmtime(p))},
            'built' if fresh else 'stale')


def methods(src):
    """Public methods parsed from lib.rs: view (&self), change (&mut self),
    init, payable. Teaching aid and the console's method chips; the deployed
    truth is always near_contract's WASM export list."""
    out, pending = [], set()
    for line in src.splitlines():
        s = line.strip()
        if s.startswith('#[init'):
            pending.add('init')
        elif s.startswith('#[payable'):
            pending.add('payable')
        elif s.startswith('#[private'):
            pending.add('private')
        m = re.match(r'pub fn (\w+)\s*\((.*)', s)
        if m:
            sig = m.group(2)
            kind = 'init' if 'init' in pending else \
                'change' if '&mut self' in sig else 'view' if '&self' in sig else 'static'
            args = [a.strip().split(':')[0] for a in sig.split(')')[0].split(',')
                    if ':' in a and 'self' not in a]
            out.append({'name': m.group(1), 'kind': kind, 'args': args,
                        'payable': 'payable' in pending, 'private': 'private' in pending})
            pending = set()
        elif s and not s.startswith('#[') and not s.startswith('///'):
            pending = set()
    return out


def projects():
    out = []
    if os.path.isdir(ROOT):
        for d in sorted(glob.glob(os.path.join(ROOT, '*'))):
            if not _is_project(d):
                continue
            pkg = os.path.basename(d)
            m = _meta(d)
            w, state = _wasm_info(pkg, d)
            out.append({'name': pkg, 'template': m.get('template'), 'desc': m.get('desc'),
                        'state': state, 'wasm_bytes': (w or {}).get('bytes'),
                        'deployed': m.get('deployed') or [], 'cairo_twin': m.get('cairo_twin'),
                        'created': m.get('created')})
    return {'projects': out, 'count': len(out), 'dir': ROOT}


def project(name):
    pkg, d = _dir(name)
    if not _is_project(d):
        raise ValueError(f'no project {pkg!r} — new it first')
    m = _meta(d)
    src = open(os.path.join(d, 'src', 'lib.rs')).read()
    w, state = _wasm_info(pkg, d)
    walk = []
    if m.get('template'):
        try:
            walk = starter(m['template']).get('walkthrough') or []
        except ValueError:
            walk = []
    # pin each walkthrough note to the line it is about, if that code survived
    lines = src.splitlines()
    for note in walk:
        note['line'] = next((i + 1 for i, l in enumerate(lines) if note['find'] in l), None)
    return {'name': pkg, 'dir': d, 'meta': m, 'state': state, 'wasm': w,
            'files': _tree(d), 'methods': methods(src), 'walkthrough': walk,
            'steps': (starter(m['template']).get('steps') if m.get('template') else None)}


def read(name, path):
    pkg, d = _dir(name)
    p = os.path.realpath(os.path.join(d, str(path)))
    if not p.startswith(os.path.realpath(d) + os.sep) or not os.path.isfile(p):
        raise ValueError(f'no file {path!r} in {pkg}')
    return {'name': pkg, 'path': str(path), 'content': open(p, errors='replace').read()}


def write(name, path, content):
    pkg, d = _dir(name)
    if not _is_project(d):
        raise ValueError(f'no project {pkg!r}')
    rel = str(path).lstrip('/')
    if rel in ('Cargo.lock', META_FILE) or rel.startswith(('target/', '.target/', '../')):
        raise ValueError(f'{rel} is not editable here')
    p = os.path.realpath(os.path.join(d, rel))
    if not p.startswith(os.path.realpath(d) + os.sep):
        raise ValueError('path escapes the project')
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        f.write(str(content))
    return {'name': pkg, 'path': rel, 'bytes': len(str(content).encode())}


# ── cargo ────────────────────────────────────────────────────────

def cargo():
    for c in (os.path.join(CARGO_BIN, 'cargo'), shutil.which('cargo')):
        if c and os.path.isfile(c):
            return c
    return None


def toolchain():
    c = cargo()
    out = {'cargo': c, 'target': WASM_TARGET, 'ok': False, 'agent': AGENT_URL}
    if not c:
        out['error'] = 'cargo not installed (rustup.rs)'
        return out
    try:
        v = subprocess.run([c, '--version'], capture_output=True, text=True, timeout=20)
        out['version'] = v.stdout.strip()
        r = subprocess.run([os.path.join(os.path.dirname(c), 'rustup'), 'target', 'list',
                            '--installed'], capture_output=True, text=True, timeout=20)
        out['wasm32'] = WASM_TARGET in r.stdout
        out['ok'] = out['wasm32']
        if not out['wasm32']:
            out['error'] = f'rustup target add {WASM_TARGET}'
    except Exception as e:  # noqa: BLE001
        out['error'] = str(e)
    return out


def _env():
    env = dict(os.environ)
    env['PATH'] = CARGO_BIN + os.pathsep + env.get('PATH', '')
    env['CARGO_TARGET_DIR'] = TARGET
    env.setdefault('CARGO_TERM_COLOR', 'never')
    return env


def _run(args, cwd, timeout):
    c = cargo()
    if not c:
        return {'ok': False, 'error': 'cargo not installed', 'log': ''}
    t0 = time.time()
    try:
        out = subprocess.run([c, *args], cwd=cwd, capture_output=True, text=True,
                             timeout=timeout, env=_env())
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'cargo timed out after {timeout}s', 'log': ''}
    log = (out.stdout + out.stderr)
    return {'ok': out.returncode == 0, 'code': out.returncode,
            'log': log[-16000:], 'seconds': round(time.time() - t0, 1)}


def diagnostics(log, d=None):
    """rustc's 'error[E0308]: msg\\n --> src/lib.rs:12:5' → rows pinned to a line."""
    out, cur = [], None
    for line in (log or '').splitlines():
        m = re.match(r'^(error|warning)(\[\w+\])?: (.*)$', line)
        if m:
            cur = {'level': m.group(1), 'message': m.group(3).strip(), 'file': None, 'line': None}
            if not cur['message'].startswith(('aborting due to', 'could not compile',
                                              'build failed', '`', 'unused')):
                out.append(cur)
            continue
        m = re.match(r'^\s*-->\s+(.+?):(\d+):(\d+)', line)
        if m and cur and cur.get('line') is None:
            f = m.group(1)
            if d and f.startswith(d):
                f = os.path.relpath(f, d)
            cur.update(file=f, line=int(m.group(2)), col=int(m.group(3)))
    return [x for x in out if x['level'] == 'error'][:20] + \
        [x for x in out if x['level'] == 'warning'][:10]


def build(name, timeout=900):
    """cargo build → .wasm. Returns the log, diagnostics pinned to file:line,
    and the artifact (size, sha256, exported methods)."""
    pkg, d = _dir(name)
    if not _is_project(d):
        raise ValueError(f'no project {pkg!r}')
    r = _run(['build', '--target', WASM_TARGET, '--release'], d, timeout)
    r['name'] = pkg
    r['diagnostics'] = diagnostics(r.get('log'), d)
    if r.get('ok'):
        w, _ = _wasm_info(pkg, d)
        r['wasm'] = w
        if w:
            os.utime(w['path'])  # the artifact is now newer than the source
    return r


def _tests(log):
    rows = []
    for m in re.finditer(r'^test (\S+)( - should panic)? \.\.\. (ok|FAILED|ignored)', log or '', re.M):
        if m.group(1) == 'result':
            continue
        rows.append({'name': m.group(1), 'status': {'ok': 'pass', 'FAILED': 'fail'}.get(
            m.group(3), m.group(3)), 'should_panic': bool(m.group(2))})
    # the panic message of each failure, for the 'why'
    for m in re.finditer(r"---- (\S+) stdout ----\n(.*?)(?=\n---- |\nfailures:|\Z)", log or '', re.S):
        why = m.group(2).strip()
        pm = re.search(r"panicked at [^\n]*\n([^\n]+)", why) or re.search(r"(expected panic.*|panic did not contain.*|assertion[^\n]*)", why)
        for r in rows:
            if r['name'] == m.group(1) and r['status'] == 'fail':
                r['reason'] = (pm.group(1) if pm else why.splitlines()[-1] if why else '')[:300]
    return rows


def verify(name, timeout=900):
    """cargo test — the unit tests inside lib.rs, in the SDK's fake runtime."""
    pkg, d = _dir(name)
    if not _is_project(d):
        raise ValueError(f'no project {pkg!r}')
    r = _run(['test', '--lib'], d, timeout)
    r['name'] = pkg
    r['tests'] = _tests(r.get('log'))
    r['passed'] = sum(1 for t in r['tests'] if t['status'] == 'pass')
    r['failed'] = sum(1 for t in r['tests'] if t['status'] == 'fail')
    r['diagnostics'] = diagnostics(r.get('log'), d) if not r.get('ok') and not r['tests'] else []
    return r


def check(name, timeout=900):
    """build + verify in one go."""
    b = build(name, timeout)
    out = {'name': b['name'], 'build': b}
    if b.get('ok'):
        out['test'] = verify(name, timeout)
    out['ok'] = bool(b.get('ok') and out.get('test', {}).get('ok'))
    return out


# ── vibe: orbit/claudecode edits the crate ──────────────────────

def _agent_goal(d):
    return (SYSTEM_PROMPT + f"\n\nWork ONLY inside {d}. Before any cargo command run: "
            f"export PATH={CARGO_BIN}:$PATH CARGO_TARGET_DIR={TARGET}")


def _agent(d, prompt, model, timeout, on_step=None):
    """POST /run/stream to orbit/claudecode. (ok, text, meta, steps) or None
    when the agent server is unreachable."""
    import urllib.error
    import urllib.request
    body = json.dumps({'query': str(prompt), 'goal': _agent_goal(d), 'path': d,
                       'model': model or None, 'timeout': int(timeout)}).encode()
    req = urllib.request.Request(AGENT_URL + '/run/stream', data=body, method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        resp = urllib.request.urlopen(req, timeout=int(timeout) + 60)
    except (urllib.error.URLError, OSError):
        return None
    steps, done, err = [], None, None
    with resp:
        for raw in resp:
            line = raw.decode('utf-8', 'replace').strip()
            if not line.startswith('data:'):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except ValueError:
                continue
            if ev.get('type') == 'step':
                st = ev.get('step') or {}
                steps.append(st)
                if on_step:
                    try:
                        on_step(step_brief(st))
                    except Exception:  # noqa: BLE001
                        pass
            elif ev.get('type') == 'done':
                done = ev
            elif ev.get('type') == 'error':
                err = ev.get('error') or 'agent error'
    last = steps[-1] if steps else {}
    usage = (done or {}).get('usage') or last.get('params', {}).get('usage') or {}
    meta = {'agent': 'orbit/claudecode', 'task_id': (done or {}).get('task_id'),
            'num_turns': usage.get('turns'), 'total_cost_usd': usage.get('cost'),
            'model': usage.get('model')}
    if err:
        return False, err, meta, steps
    if last.get('tool') == 'error':
        return False, last.get('error') or 'agent failed', meta, steps
    text = last.get('params', {}).get('summary') if last.get('tool') == 'finish' else ''
    return done is not None, text or '', meta, steps


def _cli(d, prompt, model, timeout):
    claude = shutil.which('claude') or os.path.expanduser('~/.local/bin/claude')
    if not os.path.isfile(claude):
        return False, 'claude CLI not found and agent server unreachable', {}, []
    cmd = [claude, '-p', str(prompt), '--append-system-prompt', _agent_goal(d),
           '--permission-mode', 'acceptEdits', '--add-dir', d, '--output-format', 'json']
    if model:
        cmd += ['--model', str(model)]
    try:
        out = subprocess.run(cmd, cwd=d, capture_output=True, text=True,
                             timeout=int(timeout), env=_env())
    except subprocess.TimeoutExpired:
        return False, f'vibe timed out after {timeout}s', {}, []
    text, meta = out.stdout, {'agent': 'claude-cli'}
    try:
        parsed = json.loads(out.stdout)
        text = parsed.get('result', out.stdout)
        meta.update({k: parsed.get(k) for k in ('num_turns', 'total_cost_usd', 'session_id')
                     if k in parsed})
    except Exception:  # noqa: BLE001
        pass
    return out.returncode == 0, text or out.stderr or '', meta, []


def step_brief(st):
    p = st.get('params') or {}
    what = p.get('command') or p.get('file_path') or p.get('path') or p.get('pattern') \
        or p.get('summary') or p.get('text') or ''
    return {'tool': st.get('tool') or 'step', 'what': str(what)[:240],
            'error': bool(st.get('error') or (isinstance(st.get('result'), dict)
                                              and st['result'].get('error')))}


def vibe(name, prompt, model=None, verify_after=True, timeout=1500, on_step=None):
    """Hand the crate to orbit/claudecode with the ask, then build + test."""
    pkg, d = _dir(name)
    if not _is_project(d):
        raise ValueError(f'no project {pkg!r} — new it first')
    r = _agent(d, prompt, model, timeout, on_step)
    if r is None:
        r = _cli(d, prompt, model, timeout)
    ok, text, meta, steps = r
    res = {'name': pkg, 'ok': ok, 'meta': meta, 'result': str(text or '').strip()[-6000:],
           'steps': [step_brief(s) for s in steps][-80:]}
    if verify_after and ok:
        res['build'] = build(pkg)
        if res['build'].get('ok'):
            res['test'] = verify(pkg)
    res['files'] = _tree(d)
    return res


# ── ship: the existing keystore does the signing ─────────────────

def ship(name, account_id=None, network='testnet', init_method=None, init_args=None,
         confirm=False, rpc=None):
    """Deploy the built .wasm with wallet.deploy (testnet default, mainnet
    needs confirm). init defaults to the starter's constructor unless the
    account already has this contract (redeploy = upgrade, no init)."""
    pkg, d = _dir(name)
    if not _is_project(d):
        raise ValueError(f'no project {pkg!r}')
    w, state = _wasm_info(pkg, d)
    if not w:
        raise ValueError(f'{pkg} is not built yet — build first')
    import wallet
    m = _meta(d)
    if init_method is None:
        init = m.get('init') or {}
        acct, _ = wallet.get_account(network, account_id)
        already = any(x.get('account') == acct and x.get('network') == network
                      for x in m.get('deployed') or [])
        if init.get('method') and not already:
            init_method, init_args = init['method'], init_args or init.get('args') or {}
    out = wallet.deploy(None, w['path'], None, account_id, init_method or None,
                        init_args, network, rpc, bool(confirm))
    m.setdefault('deployed', []).append({
        'account': out.get('deployed_to'), 'network': network, 'tx': out.get('hash')
        or out.get('tx_hash') or out.get('transaction_hash'), 'at': int(time.time()),
        'sha256': w['sha256'], 'init': init_method or None})
    m['deployed'] = m['deployed'][-20:]
    _save_meta(d, m)
    out.update(project=pkg, state=state, wasm_bytes=w['bytes'])
    return out


# ── helpers for the ZK-flavoured starters ────────────────────────

def _h(b):
    return hashlib.sha256(b).digest()


def merkle(accounts):
    """Sorted-pair sha256 tree over account ids — the exact scheme the
    merkle_allowlist starter verifies. Returns the root and a proof per leaf."""
    if isinstance(accounts, str):
        accounts = [a.strip() for a in re.split(r'[,\s]+', accounts) if a.strip()]
    ids = list(dict.fromkeys(str(a).strip().lower() for a in accounts if str(a).strip()))
    if not ids:
        raise ValueError('give at least one account id')
    leaves = [_h(a.encode()) for a in ids]
    proofs = {a: [] for a in ids}
    idx = {i: [a] for i, a in enumerate(ids)}      # node position -> members below it
    level = leaves[:]
    while len(level) > 1:
        nxt, nidx = [], {}
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                a, b = level[i], level[i + 1]
                for m in idx[i]:
                    proofs[m].append(b.hex())
                for m in idx[i + 1]:
                    proofs[m].append(a.hex())
                lo, hi = (a, b) if a <= b else (b, a)
                nxt.append(_h(lo + hi))
                nidx[len(nxt) - 1] = idx[i] + idx[i + 1]
            else:                                   # odd leaf is promoted unchanged
                nxt.append(level[i])
                nidx[len(nxt) - 1] = idx[i]
        level, idx = nxt, nidx
    return {'root': level[0].hex(), 'count': len(ids),
            'leaves': {a: {'leaf': leaves[i].hex(), 'proof': proofs[a]}
                       for i, a in enumerate(ids)},
            'scheme': 'leaf=sha256(id); parent=sha256(min||max); odd leaf promoted'}


def seal(contract, who, value, salt):
    """The commit_reveal starter's seal: sha256('contract|who|value|salt')."""
    msg = f'{str(contract).lower()}|{str(who).lower()}|{int(value)}|{salt}'
    return {'seal': hashlib.sha256(msg.encode()).hexdigest(), 'message': msg}


# ── jobs ─────────────────────────────────────────────────────────

class Jobs:
    """build / verify / check / vibe on a thread, one row each, persisted."""
    FNS = {'build': build, 'verify': verify, 'check': check, 'vibe': vibe}

    def __init__(self, path=None):
        self.path = path or os.path.join(ROOT, '.jobs.json')
        self.lock = threading.Lock()
        self.rows = {}
        try:
            for r in json.load(open(self.path)).get('jobs', []):
                if r.get('state') in ('queued', 'running'):
                    r['state'], r['error'] = 'interrupted', 'server restarted'
                self.rows[r['id']] = r
        except Exception:  # noqa: BLE001
            pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            rows = sorted(self.rows.values(), key=lambda r: -r['started'])[:200]
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as f:
                json.dump({'jobs': rows}, f)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            pass

    def start(self, fn, kw):
        if fn not in self.FNS:
            raise ValueError(f'no job kind {fn!r}')
        job = {'id': uuid.uuid4().hex[:10], 'fn': fn, 'name': kw.get('name'),
               'state': 'queued', 'started': time.time(), 'steps': [],
               'prompt': (kw.get('prompt') or '')[:400] or None}
        with self.lock:
            self.rows[job['id']] = job
            self._save()

        def on_step(st):
            with self.lock:
                job['steps'].append(st)
                job['steps'] = job['steps'][-200:]

        def work():
            job['state'] = 'running'
            try:
                args = dict(kw)
                if fn == 'vibe':
                    args['on_step'] = on_step
                job['result'] = self.FNS[fn](**args)
                job['state'] = 'done' if job['result'].get('ok', True) else 'failed'
            except Exception as e:  # noqa: BLE001
                job['state'], job['error'] = 'failed', f'{type(e).__name__}: {e}'
            job['ended'] = time.time()
            with self.lock:
                self._save()
        threading.Thread(target=work, daemon=True).start()
        return self.brief(job)

    @staticmethod
    def brief(job):
        return {k: v for k, v in job.items() if k != 'result'} | \
            {'seconds': round((job.get('ended') or time.time()) - job['started'], 1)}

    def get(self, id_):
        job = self.rows.get(str(id_))
        if not job:
            return {'error': f'no job {id_}'}
        return dict(job, seconds=round((job.get('ended') or time.time()) - job['started'], 1))

    def list(self, name=None, limit=30):
        rows = sorted(self.rows.values(), key=lambda r: -r['started'])
        if name:
            rows = [r for r in rows if r.get('name') == name]
        return {'jobs': [self.brief(r) for r in rows[:int(limit)]]}


JOBS = None


def jobs():
    global JOBS
    if JOBS is None:
        JOBS = Jobs()
    return JOBS
