"""frameworks.py — one adapter per ZK toolchain zkworld can drive.

zkworld started as the cairo workbench. Everything that is specific to one
language/toolchain lives here behind a small interface, so the anchor
(src/mod.py) only orchestrates: catalog, files, fork, vibe, jobs, server.

    id        framework key, stored as `framework` in each project's config.json
    marker    the file that makes a dir a project of this framework
    tools     binaries it needs (looked up in ~/.local/bin first, then PATH)
    sandbox   True when projects get a live chain app (deploy/call/events)

and the verbs every adapter implements:

    detect(d) -> bool           is d a project of mine (marker present)
    sources(d) -> [paths]       files whose mtime decides fresh/stale
    build_state(d)              unbuilt | stale | fresh
    build(pkg, d, timeout)      {ok, log, diagnostics[], artifacts[]}
    test(pkg, d, timeout)       {ok, log, tests[], totals}
    abi(pkg, d)                 {contracts[] | circuits[]} — what the app renders
    scaffold(pkg, d, tid, ctx)  write a starter project
    rename(d, old, new)         fix package/type names after fork()
    prompt                      the vibe agent's system rules
    clean_dirs                  build output clean() removes

Adding a framework = one subclass + starters in src/templates/<id>/<starter>/.
Python stdlib only, like the rest of the module.
"""

import glob
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time

SRC = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(SRC, 'templates')
BIN = os.path.expanduser('~/.local/bin')
EXTRA_BINS = [BIN, os.path.expanduser('~/.cargo/bin'),
              os.path.expanduser('~/.nargo/bin'), os.path.expanduser('~/.foundry/bin'),
              os.path.expanduser('~/.zokrates/bin'), '/usr/local/go/bin',
              os.path.expanduser('~/go/bin')]


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def tool(name):
    """Absolute path to a toolchain binary, the user-local bins first."""
    for b in EXTRA_BINS:
        p = os.path.join(b, name)
        if os.path.exists(p):
            return p
    return shutil.which(name)


def env():
    e = dict(os.environ)
    e['PATH'] = os.pathsep.join(EXTRA_BINS + [e.get('PATH', '')])
    return e


def version(name):
    exe = tool(name)
    if not exe:
        return None
    try:
        out = subprocess.run([exe, '--version'], capture_output=True, text=True,
                             timeout=15, env=env())
        txt = (out.stdout or out.stderr).strip()
        return txt.splitlines()[0] if txt else None
    except Exception as e:  # noqa: BLE001
        return f'error: {e}'


def run(name, args, cwd, timeout=900, clean=None):
    exe = tool(name)
    if not exe:
        return {'ok': False, 'error': f'{name} not installed'}
    try:
        out = subprocess.run([exe, *args], cwd=cwd, capture_output=True,
                             text=True, timeout=timeout, env=env())
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'{name} timed out after {timeout}s'}
    log = out.stdout + out.stderr
    return {'ok': out.returncode == 0, 'code': out.returncode,
            'log': (clean(log) if clean else log.strip())[-12000:]}


def pascal(pkg):
    return ''.join(p.capitalize() for p in pkg.split('_') if p)


def _read(p):
    try:
        with open(p) as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return None


def _emit(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(content)


def _newest(paths):
    return max((os.path.getmtime(p) for p in paths if os.path.exists(p)), default=0)


# ---------------------------------------------------------------------------
# the interface
# ---------------------------------------------------------------------------

class Framework:
    id = ''
    title = ''
    lang = ''
    blurb = ''
    marker = ''
    ext = ''
    tools = ()
    install = ''
    homepage = ''
    kind = 'dsl'          # dsl | zkvm | library — see CATALOG
    proof = ''            # the proof system(s) it targets
    needle = ''           # marker must also contain this (go.mod, Cargo.toml)
    sandbox = False
    clean_dirs = ('target',)
    default_template = None
    # dest path of each starter file (template-relative name -> project path)
    layout = {}
    prompt = ''

    # -- discovery --
    def detect(self, d):
        m = os.path.join(d, self.marker)
        return os.path.isfile(m) and (not self.needle or self.needle in (_read(m) or ''))

    def sources(self, d):
        return [os.path.join(d, self.marker)] + glob.glob(
            os.path.join(d, '**', f'*{self.ext}'), recursive=True)

    def tests(self, d):
        return glob.glob(os.path.join(d, 'tests', '**', '*'), recursive=True)

    def artifacts(self, d):
        return []

    def build_state(self, d):
        arts = self.artifacts(d)
        if not arts:
            return 'unbuilt'
        built = min(os.path.getmtime(a) for a in arts)
        srcs = [s for s in self.sources(d) if '/target/' not in s]
        return 'stale' if _newest(srcs) > built else 'fresh'

    def installed(self):
        return all(tool(t) for t in self.tools)

    def toolchain(self):
        return {t: {'path': tool(t), 'version': version(t) if tool(t) else None}
                for t in self.tools}

    def describe(self, deep=False):
        out = {'id': self.id, 'title': self.title, 'lang': self.lang,
               'blurb': self.blurb, 'marker': self.marker, 'ext': self.ext,
               'tools': list(self.tools), 'installed': self.installed(),
               'install': self.install, 'homepage': self.homepage,
               'kind': self.kind, 'proof': self.proof, 'driven': True,
               'sandbox': self.sandbox, 'runs': self.runs,
               'templates': [t['id'] for t in self.templates()]}
        if deep:
            out['toolchain'] = self.toolchain()
        return out

    # -- starters --
    def templates(self):
        out = []
        for d in sorted(glob.glob(os.path.join(TEMPLATES, self.id, '*', 'meta.json'))):
            tdir = os.path.dirname(d)
            try:
                meta = json.load(open(d))
            except Exception:  # noqa: BLE001
                meta = {}
            tid = os.path.basename(tdir)
            out.append({'id': tid, 'framework': self.id,
                        'title': meta.get('title', tid),
                        'blurb': meta.get('blurb', ''),
                        'order': meta.get('order', 99)})
        return sorted(out, key=lambda t: (t['order'], t['id']))

    def render(self, tid, name, ctx):
        # per-starter file first, the framework's shared file as fallback
        p = os.path.join(TEMPLATES, self.id, tid, name)
        if not os.path.exists(p):
            p = os.path.join(TEMPLATES, self.id, name)
        s = _read(p)
        if s is None:
            return None
        for k, v in ctx.items():
            s = s.replace('{{' + k + '}}', v)
        return s

    def scaffold(self, pkg, d, tid, ctx):
        written = []
        for src, dest in self.layout.items():
            s = self.render(tid, src, ctx)
            if s is None:
                continue
            dest = dest.replace('{PKG}', pkg)
            _emit(os.path.join(d, dest), s)
            written.append(dest)
        app = self.render(tid, 'app.json', ctx)
        if app is not None:
            _emit(os.path.join(d, 'app.json'), app)
        _emit(os.path.join(d, '.gitignore'),
              ''.join(f'{x}/\n' for x in self.clean_dirs))
        return written

    def default_app(self):
        """The page /zkworld/<name>/ serves when a project ships no app/:
        the framework's own, else the shared circuit page."""
        own = os.path.join(TEMPLATES, self.id, 'app.html')
        return own if os.path.exists(own) else os.path.join(TEMPLATES, 'circuit.html')

    # -- fork --
    def rename(self, d, old, new):
        """Package-level renames after a copy. Default: the package name in
        the marker file, plus `old` as a whole word in sources."""
        word = re.compile(r'\b' + re.escape(old) + r'\b')
        for p in self.sources(d):
            if os.path.isfile(p) and p != os.path.join(d, self.marker):
                _emit(p, word.sub(new, _read(p) or ''))
        m = os.path.join(d, self.marker)
        _emit(m, re.sub(r'(?m)^name\s*=\s*"[^"]*"', f'name = "{new}"',
                        _read(m) or '', count=1))

    # -- verbs --
    def build(self, pkg, d, timeout=900):
        return {'ok': False, 'error': f'{self.id}: build not implemented'}

    def test(self, pkg, d, timeout=1200):
        return {'ok': False, 'error': f'{self.id}: test not implemented'}

    def abi(self, pkg, d):
        return {'name': pkg, 'contracts': []}

    def witness(self, pkg, d, inputs):
        return {'ok': False, 'error': f'{self.id} has no witness runner'}

    @property
    def runs(self):
        """True when witness() executes the program on inputs, which is what
        makes a project comparable with its ports in other frameworks."""
        return type(self).witness is not Framework.witness

    # -- the shared statement format: tests/*.json, any framework ----------
    #
    # A case is {"input": {name: value}, "expect": {name: value}} (must be
    # accepted, and every expected name must match) or {"input": ..., "fail":
    # true} (must be rejected). Values are decimal strings or arrays of them.
    # Frameworks differ on whether a public value is an OUTPUT (circom's
    # `c <== a*b`) or a public INPUT (noir/gnark/zokrates `c: pub Field`), so
    # an expected name the program takes as an input is fed in, and acceptance
    # is the check. One case file therefore runs unchanged in every framework;
    # that is what port() and compare() lean on.

    def input_names(self, pkg, d):
        names = []
        for c in self.abi(pkg, d).get('circuits', [])[:1]:
            names = [i['name'] for i in c.get('inputs', [])]
        return names

    def execute(self, pkg, d, case):
        """Run one statement case -> {ok, outputs|error, fed}."""
        shape = (self.abi(pkg, d).get('circuits') or [{}])[0]
        names = [i['name'] for i in shape.get('inputs', [])]
        outs = {o['name'] for o in shape.get('outputs', [])}
        inputs = dict(case.get('input') or {})
        expect = dict(case.get('expect') or {})
        for k in list(expect):
            if k in names and k not in inputs:
                inputs[k] = expect.pop(k)
        # ...and the mirror image: a value given as an input that this
        # program computes as an output is a claim to check, not to feed
        for k in list(inputs):
            if k in outs and k not in names:
                expect.setdefault(k, inputs.pop(k))
        if names:
            inputs = {k: v for k, v in inputs.items() if k in names}
        r = self.witness(pkg, d, inputs)
        r['checked'] = expect
        got = r.get('outputs') or {}
        wrong = {k: got.get(k) for k, v in expect.items() if _norm(got.get(k)) != _norm(v)}
        r['holds'] = bool(r.get('ok')) and not wrong
        if r.get('ok') and wrong:
            r['why'] = '; '.join(f'computes {k}={g}, not {expect[k]}' for k, g in wrong.items())
        return r

    def case_files(self, d):
        return sorted(glob.glob(os.path.join(d, 'tests', '*.json')))

    def case_tests(self, pkg, d):
        """Every tests/*.json case through witness(); [] when none."""
        tests = []
        for c in self.case_files(d):
            name = os.path.splitext(os.path.basename(c))[0]
            try:
                spec = json.load(open(c))
            except Exception as e:  # noqa: BLE001
                tests.append({'status': 'fail', 'name': name, 'reason': f'bad json: {e}'})
                continue
            for i, case in enumerate(spec if isinstance(spec, list) else [spec]):
                label = case.get('name') or (name if not isinstance(spec, list) else f'{name}[{i}]')
                r = self.execute(pkg, d, case)
                outs = r.get('outputs') or {}
                if case.get('fail'):
                    # the claim (input -> expect) must not hold: rejected, or
                    # accepted with a different answer (circom computes c, so
                    # "3*5 = 16" comes back as c=15 rather than a rejection)
                    ok = not r.get('ok') or any(_norm(outs.get(k)) != _norm(v)
                                                for k, v in r['checked'].items())
                    reason = None if ok else f"expected a rejection, got {outs}"
                elif not r.get('ok'):
                    ok, reason = False, r.get('error')
                else:
                    bad = {k: (str(v), outs.get(k)) for k, v in r['checked'].items()
                           if _norm(outs.get(k)) != _norm(v)}
                    ok = not bad
                    reason = '; '.join(f'{k}: want {w}, got {g}' for k, (w, g) in bad.items()) or None
                t = {'status': 'pass' if ok else 'fail', 'name': label, 'gas': None,
                     'source': 'case'}
                if reason:
                    t['reason'] = reason
                tests.append(t)
        return tests

    def merge_tests(self, native, cases):
        """A native runner's result + the statement cases -> one result."""
        tests = list(native.get('tests') or []) + cases
        passed = sum(t['status'] == 'pass' for t in tests)
        log = (native.get('log') or native.get('error') or '').rstrip()
        if cases:
            log += ('\n' if log else '') + '\n'.join(
                f"[{t['status'].upper()}] {t['name']}" + (f"  ({t['reason']})" if t.get('reason') else '')
                for t in cases)
        totals = {'passed': passed, 'failed': len(tests) - passed, 'skipped': 0, 'ignored': 0}
        ok = bool(tests) and passed == len(tests) and native.get('ok', True) is not False
        return {**native, 'ok': ok, 'tests': tests, 'totals': totals if tests else None,
                'log': log or 'no tests', 'diagnostics': native.get('diagnostics', [])}


# ---------------------------------------------------------------------------
# Cairo / Starknet — scarb + starknet-foundry, live devnet sandbox
# ---------------------------------------------------------------------------

def _short(ty):
    """core::integer::u256 -> u256, Span<core::felt252> -> Span<felt252>."""
    ty = (ty or '').replace('::<', '<')
    return re.sub(r'\b(?:[A-Za-z_][A-Za-z0-9_]*::)+', '', ty)


# snforge recompiles its own Rust scarb plugin and newer rustc spams lifetime
# lints about it. Those blocks are noise about foundry, never about the
# user's contract, so drop them before the log reaches a human.
_RUST_WARN = re.compile(r'^warning: .*?(?=^\S)', re.S | re.M)
_TEST_LINE = re.compile(
    r'^\[(PASS|FAIL|IGNORE|SKIP)\]\s+(\S+)(?:\s+\((?:l1_gas: ~\d+, l1_data_gas: ~\d+, l2_gas: ~)?'
    r'(?:gas: ~)?(\d+)\))?', re.M)
_TEST_TOTAL = re.compile(
    r'^Tests: (\d+) passed, (\d+) failed, (\d+) skipped, (\d+) ignored', re.M)
_FAIL_BLOCK = re.compile(r'^Failure data:\n(.*?)(?=^\S|\Z)', re.S | re.M)
# Cairo / rustc-style diagnostics: "error: ...\n --> path:line:col"
_DIAG = re.compile(r'^(error|warning)(?:\[\w+\])?: (.+?)\n\s*--> ([^:\n]+):(\d+):(\d+)', re.M)


def clean_log(log):
    # everything between cargo starting and finishing the plugin is rustc's
    _plugin = re.compile(r'^(\s*Compiling snforge_scarb_plugin[^\n]*\n).*?'
                         r'^(\s*Finished `release`[^\n]*\n)', re.S | re.M)
    log = _plugin.sub(r'\1\2', log or '')
    log = _RUST_WARN.sub('', log)
    return re.sub(r'\n{3,}', '\n\n', log).strip()


def diagnostics(log, root, pattern=_DIAG):
    out = []
    for m in pattern.finditer(log or ''):
        path = m.group(3)
        if os.path.isabs(path):
            path = os.path.relpath(path, root)
        if path.startswith('..'):
            continue  # inside a dependency, not the user's code
        out.append({'level': m.group(1), 'message': m.group(2).strip(),
                    'path': path, 'line': int(m.group(4)),
                    'col': int(m.group(5))})
    return out


def _summarize_snforge(log):
    tests = []
    for m in _TEST_LINE.finditer(log or ''):
        full = m.group(2)
        # pkg_integrationtest::test_file::test_name -> test_file::test_name
        short = '::'.join(full.split('::')[1:]) or full
        tests.append({'status': m.group(1).lower(), 'name': short, 'full': full,
                      'gas': int(m.group(3)) if m.group(3) else None})
    # snforge prints each failure's panic data right after its [FAIL] line
    fails = _FAIL_BLOCK.findall(log or '')
    for t, why in zip((t for t in tests if t['status'] == 'fail'), fails):
        why = why.strip()
        # panic data prints as `0x77616e74 ('want')`; keep the readable part
        quoted = re.findall(r"\('([^']*)'\)", why)
        t['reason'] = ', '.join(quoted) if quoted else why
    tot = _TEST_TOTAL.search(log or '')
    totals = None
    if tot:
        totals = dict(zip(('passed', 'failed', 'skipped', 'ignored'),
                          map(int, tot.groups())))
    return {'tests': tests, 'totals': totals}


class Cairo(Framework):
    id = 'cairo'
    title = 'Cairo · Starknet'
    lang = 'Cairo 2'
    blurb = ('STARK-provable contracts for Starknet. scarb builds Sierra/CASM, '
             'snforge tests them, every contract gets a live app on a local devnet.')
    marker = 'Scarb.toml'
    ext = '.cairo'
    tools = ('scarb', 'snforge', 'sncast', 'universal-sierra-compiler')
    install = 'curl -L https://raw.githubusercontent.com/software-mansion/scarb/main/install.sh | sh  (scarb 2.8.4) + snfoundryup -v 0.31.0'
    homepage = 'https://www.cairo-lang.org'
    sandbox = True
    clean_dirs = ('target', '.snfoundry_cache')
    default_template = 'counter'
    layout = {'Scarb.toml.tmpl': 'Scarb.toml',
              'lib.cairo.tmpl': 'src/lib.cairo',
              'test.cairo.tmpl': 'tests/test_{PKG}.cairo'}
    prompt = (
        "You are a Cairo smart-contract engineer working inside a single Scarb "
        "project (the current working directory). Target Cairo 2.8.4, edition "
        "2024_07, starknet 2.8.4, and starknet-foundry (snforge) 0.31 for tests.\n"
        "Rules:\n"
        "- The package name is the directory name; the entry file is src/lib.cairo "
        "and tests live in tests/*.cairo.\n"
        "- A contract is a `#[starknet::contract] mod ...` with a `#[storage] "
        "struct Storage`, storage access via StoragePointerReadAccess / "
        "StoragePointerWriteAccess, and a `#[starknet::interface] trait` embedded "
        "with `#[abi(embed_v0)]`.\n"
        "- Tests use snforge_std: `declare(\"Name\").unwrap().contract_class()"
        ".deploy(@calldata).unwrap()` then the generated `I<Name>Dispatcher`.\n"
        "- Edit files directly, then ALWAYS run `scarb build` and `snforge test` "
        "to prove your change compiles and passes before you finish. Fix any "
        "compiler error you introduce.\n"
        "- Keep the public interface coherent and comment WHY, not WHAT.\n"
        "- The contract's web app is app/index.html (plain HTML, no build step). "
        "If it does not exist the shared default ABI-driven page is served; only "
        "write app/index.html when asked for a custom app. It talks to "
        "<base>/_api/{app,call,deploy,events}?name=<pkg>.\n"
        "- app.json (next to Scarb.toml) is the plain-language layer of that "
        "default page: friendly labels, hidden inputs/outputs, visuals (show: "
        "big|meter|chart|yesno|address|text|list|hide), event sentences. When you "
        "rename or add an entrypoint, keep app.json in sync. Reference: "
        "docs/simple-apps.md in the zkworld module."
    )

    def sources(self, d):
        return [os.path.join(d, 'Scarb.toml')] + glob.glob(
            os.path.join(d, 'src', '**', '*.cairo'), recursive=True)

    def tests(self, d):
        return glob.glob(os.path.join(d, 'tests', '**', '*.cairo'), recursive=True)

    def artifacts(self, d):
        arts = glob.glob(os.path.join(d, 'target', 'dev', '*.contract_class.json'))
        return [a for a in arts if '.test.' not in a]

    def build(self, pkg, d, timeout=900):
        r = run('scarb', ['build'], d, timeout, clean_log)
        r['diagnostics'] = diagnostics(r.get('log'), d)
        if r.get('ok'):
            r['artifacts'] = self.abi(pkg, d).get('contracts', [])
        return r

    def test(self, pkg, d, timeout=1200):
        r = run('snforge', ['test'], d, timeout, clean_log)
        r.update(_summarize_snforge(r.get('log')))
        r['diagnostics'] = diagnostics(r.get('log'), d)
        return r

    def abi(self, pkg, d):
        dev = os.path.join(d, 'target', 'dev')
        out = []
        for p in sorted(glob.glob(os.path.join(dev, '*.contract_class.json'))):
            base = os.path.basename(p)
            if '.test.' in base or base.endswith('.compiled_contract_class.json'):
                continue
            try:
                cls = json.load(open(p))
            except Exception:  # noqa: BLE001
                continue
            contract = base[len(pkg) + 1:].split('.')[0] \
                if base.startswith(pkg + '_') else base.split('.')[0]
            fns, events, ctor = [], [], None
            for item in cls.get('abi', []):
                if item.get('type') == 'interface':
                    for f in item.get('items', []):
                        fns.append(self._abi_fn(f, item['name']))
                elif item.get('type') == 'function':
                    fns.append(self._abi_fn(item, None))
                elif item.get('type') == 'constructor':
                    ctor = [{'name': i['name'], 'type': _short(i['type'])}
                            for i in item.get('inputs', [])]
                elif item.get('type') == 'event' and item.get('kind') == 'struct':
                    events.append({'name': item['name'].split('::')[-1],
                                   'fields': [{'name': m['name'],
                                               'type': _short(m['type']),
                                               'key': m.get('kind') == 'key'}
                                              for m in item.get('members', [])]})
            casm = p.replace('.contract_class.json', '.compiled_contract_class.json')
            casm_len = None
            if os.path.exists(casm):
                try:
                    casm_len = len(json.load(open(casm)).get('bytecode', []))
                except Exception:  # noqa: BLE001
                    pass
            out.append({'contract': contract,
                        'constructor': ctor,
                        'functions': fns,
                        'events': events,
                        'sierra_felts': len(cls.get('sierra_program', [])),
                        'casm_felts': casm_len,
                        'artifact': os.path.relpath(p, d)})
        if not out:
            return {'name': pkg, 'contracts': [], 'error': 'not built yet'}
        return {'name': pkg, 'state': self.build_state(d), 'contracts': out}

    @staticmethod
    def _abi_fn(f, iface):
        return {'name': f['name'],
                'interface': iface.split('::')[-1] if iface else None,
                'mutability': f.get('state_mutability', 'external'),
                'inputs': [{'name': i['name'], 'type': _short(i['type'])}
                           for i in f.get('inputs', [])],
                'outputs': [_short(o['type']) for o in f.get('outputs', [])]}

    def rename(self, d, old, new):
        """Package, contract module (PascalCase), dispatchers and `use <pkg>::`
        paths, plus test filenames, so the copy builds and tests on its own."""
        o, n = pascal(old), pascal(new)
        # `Old` alone or as a prefix of a type (IOld, OldDispatcher, ...)
        cls_re = re.compile(r'(?<![A-Za-z0-9_])(I?)' + re.escape(o) + r'(?![a-z0-9_])')
        pkg_re = re.compile(r'\b' + re.escape(old) + r'::')
        for p in glob.glob(os.path.join(d, '**', '*.cairo'), recursive=True):
            s = _read(p) or ''
            _emit(p, cls_re.sub(lambda m: m.group(1) + n, pkg_re.sub(new + '::', s)))
            base = os.path.basename(p)
            if old in base:
                os.rename(p, os.path.join(os.path.dirname(p), base.replace(old, new)))
        toml = os.path.join(d, 'Scarb.toml')
        _emit(toml, re.sub(r'(?m)^name = "[^"]*"', f'name = "{new}"',
                           _read(toml) or '', count=1))


# ---------------------------------------------------------------------------
# Circom — R1CS circuits; witnesses computed with circom's own wasm + node
# ---------------------------------------------------------------------------

# circom errors: "error[P1012]: msg\n   ┌─ "main.circom":5:3"
_CIRCOM_DIAG = re.compile(
    r'^(error|warning)(?:\[\w+\])?: (.+?)\n\s*\S+\s+"?([^":\n]+)"?:(\d+):(\d+)', re.M)
_CIRCOM_INFO = {
    'template_instances': r'template instances: (\d+)',
    'non_linear_constraints': r'non-linear constraints: (\d+)',
    'linear_constraints': r'linear constraints: (\d+)',
    'public_inputs': r'public inputs: (\d+)',
    'private_inputs': r'private inputs: (\d+)',
    'public_outputs': r'public outputs: (\d+)',
    'wires': r'wires: (\d+)',
    'labels': r'labels: (\d+)',
}


def _read_wtns(path):
    """circom .wtns -> [int]. Layout: 'wtns' u32 version u32 nSections, then
    sections (u32 type, u64 size): 1 = header (u32 n8, prime, u32 n), 2 =
    n little-endian field elements of n8 bytes."""
    with open(path, 'rb') as f:
        b = f.read()
    if b[:4] != b'wtns':
        raise ValueError('not a wtns file')
    _, nsec = struct.unpack_from('<II', b, 4)
    off, n8, n, data = 12, None, None, None
    for _ in range(nsec):
        typ, size = struct.unpack_from('<IQ', b, off)
        off += 12
        if typ == 1:
            n8 = struct.unpack_from('<I', b, off)[0]
            n = struct.unpack_from('<I', b, off + 4 + n8)[0]
        elif typ == 2:
            data = b[off:off + size]
        off += size
    return [int.from_bytes(data[i * n8:(i + 1) * n8], 'little') for i in range(n)]


def _read_sym(path):
    """label_idx,witness_idx,component_idx,name -> {name: witness_idx}."""
    out = {}
    for line in (_read(path) or '').splitlines():
        parts = line.split(',', 3)
        if len(parts) == 4 and parts[1].lstrip('-').isdigit():
            out[parts[3]] = int(parts[1])
    return out


class Circom(Framework):
    id = 'circom'
    title = 'Circom'
    lang = 'Circom 2'
    blurb = ('Arithmetic circuits compiled to R1CS for Groth16/PLONK. circom '
             'builds the constraints + a wasm witness generator; tests are '
             'input/expected-output JSON cases run through the real witness.')
    marker = 'main.circom'
    ext = '.circom'
    tools = ('circom', 'node')
    install = 'cargo install --git https://github.com/iden3/circom circom  (needs node for witnesses; snarkjs optional for proofs)'
    homepage = 'https://docs.circom.io'
    default_template = 'multiplier'
    layout = {'main.circom.tmpl': 'main.circom',
              'Circom.toml.tmpl': 'Circom.toml',
              'test.json.tmpl': 'tests/basic.json'}
    prompt = (
        "You are a zero-knowledge circuit engineer working inside a single "
        "Circom 2 project (the current working directory).\n"
        "Rules:\n"
        "- The entry file is main.circom: `pragma circom 2.1.6;`, templates, "
        "and exactly one `component main {public [...]} = T(...);`. Put helper "
        "templates in lib/*.circom and `include \"lib/x.circom\";` them.\n"
        "- Every signal that matters must be CONSTRAINED (=== or <==); `<--` "
        "alone is an unconstrained hint and a soundness bug. Comment WHY.\n"
        "- Tests are JSON files in tests/: {\"input\": {signal: value|[...]}, "
        "\"expect\": {output_signal: value}} for a case that must pass, or "
        "{\"input\": {...}, \"fail\": true} for one that must violate a "
        "constraint. Values are decimal strings.\n"
        "- Build: `circom main.circom --r1cs --wasm --sym -o target -l lib`. "
        "Witness: `node target/main_js/generate_witness.js "
        "target/main_js/main.wasm input.json out.wtns`. ALWAYS build and run "
        "every test case before you finish.\n"
        "- Circom.toml holds the package name; keep it.\n"
    )

    def sources(self, d):
        return [s for s in glob.glob(os.path.join(d, '**', '*.circom'), recursive=True)
                if '/target/' not in s]

    def tests(self, d):
        return sorted(glob.glob(os.path.join(d, 'tests', '*.json')))

    def artifacts(self, d):
        arts = [os.path.join(d, 'target', 'main.r1cs'),
                os.path.join(d, 'target', 'main_js', 'main.wasm')]
        return arts if all(os.path.exists(a) for a in arts) else []

    def build(self, pkg, d, timeout=900):
        os.makedirs(os.path.join(d, 'target'), exist_ok=True)
        args = ['main.circom', '--r1cs', '--wasm', '--sym', '-o', 'target']
        for lib in ('lib', 'node_modules'):
            if os.path.isdir(os.path.join(d, lib)):
                args += ['-l', lib]
        r = run('circom', args, d, timeout)
        r['diagnostics'] = diagnostics(r.get('log'), d, _CIRCOM_DIAG)
        if r.get('ok'):
            info = {k: int(m.group(1)) for k, pat in _CIRCOM_INFO.items()
                    if (m := re.search(pat, r.get('log') or ''))}
            _emit(os.path.join(d, 'target', 'info.json'), json.dumps(info, indent=2))
            r['artifacts'] = self.abi(pkg, d).get('circuits', [])
        return r

    def _main(self, d):
        """(template name, [public input names], signals of that template)."""
        src = _read(os.path.join(d, 'main.circom')) or ''
        m = re.search(r'component\s+main\s*(?:\{\s*public\s*\[([^\]]*)\]\s*\})?\s*=\s*(\w+)\s*\(', src)
        if not m:
            return None, [], []
        public = [x.strip() for x in (m.group(1) or '').split(',') if x.strip()]
        name = m.group(2)
        # the template body: from `template Name(` to the matching brace
        t = re.search(r'template\s+' + re.escape(name) + r'\s*\([^)]*\)\s*\{', src)
        sigs = []
        if t:
            depth, i = 1, t.end()
            while i < len(src) and depth:
                depth += {'{': 1, '}': -1}.get(src[i], 0)
                i += 1
            body = src[t.end():i]
            for s in re.finditer(r'signal\s+(input|output)\s+(\w+)((?:\s*\[[^\]]*\])*)', body):
                sigs.append({'name': s.group(2), 'kind': s.group(1),
                             'dims': [x.strip() for x in re.findall(r'\[([^\]]*)\]', s.group(3))]})
        return name, public, sigs

    def abi(self, pkg, d):
        name, public, sigs = self._main(d)
        if not name:
            return {'name': pkg, 'circuits': [], 'contracts': [],
                    'error': 'no `component main` in main.circom'}
        info = {}
        try:
            info = json.load(open(os.path.join(d, 'target', 'info.json')))
        except Exception:  # noqa: BLE001
            pass
        inputs = [{'name': s['name'], 'dims': s['dims'],
                   'public': s['name'] in public} for s in sigs if s['kind'] == 'input']
        outputs = [{'name': s['name'], 'dims': s['dims']} for s in sigs if s['kind'] == 'output']
        return {'name': pkg, 'state': self.build_state(d), 'contracts': [],
                'circuits': [{'circuit': name, 'inputs': inputs, 'outputs': outputs,
                              'constraints': (info.get('non_linear_constraints', 0)
                                              + info.get('linear_constraints', 0)) if info else None,
                              'info': info, 'artifact': 'target/main.r1cs'}]}

    def witness(self, pkg, d, inputs):
        """Run the circuit on `inputs`: ok=False when a constraint fails, else
        the values of main's output signals (decimal strings)."""
        if self.build_state(d) == 'unbuilt':
            return {'ok': False, 'error': 'build it first'}
        js = os.path.join(d, 'target', 'main_js')
        with tempfile.TemporaryDirectory() as tmp:
            inp, out = os.path.join(tmp, 'input.json'), os.path.join(tmp, 'out.wtns')
            _emit(inp, json.dumps(inputs or {}))
            t0 = time.time()
            r = run('node', [os.path.join(js, 'generate_witness.js'),
                             os.path.join(js, 'main.wasm'), inp, out], d, 120)
            if not r.get('ok') or not os.path.exists(out):
                log = r.get('log') or r.get('error') or ''
                # "Error: Error: Assert Failed.\nError in template T_3 line: 47"
                why = re.search(r'Error: (?:Error: )?(.*)', log)
                at = re.search(r'Error in template (\w+?)(?:_\d+)? line: (\d+)', log)
                msg = (why.group(1) if why else log[-600:]).strip()
                if at:
                    msg += f' (template {at.group(1)}, line {at.group(2)})'
                return {'ok': False, 'error': msg, 'line': int(at.group(2)) if at else None,
                        'log': log[-3000:]}
            w = _read_wtns(out)
        sym = _read_sym(os.path.join(d, 'target', 'main.sym'))
        _, _, sigs = self._main(d)
        outs = {}
        for s in sigs:
            if s['kind'] != 'output':
                continue
            if s['dims']:  # arrays: collect main.out[0], main.out[1], ...
                keys = sorted((k for k in sym if re.fullmatch(
                    r'main\.' + re.escape(s['name']) + r'(\[\d+\])+', k)),
                    key=lambda k: [int(x) for x in re.findall(r'\d+', k[len('main.' + s['name']):])])
                outs[s['name']] = [str(w[sym[k]]) for k in keys if sym[k] >= 0]
            elif sym.get('main.' + s['name'], -1) >= 0:
                outs[s['name']] = str(w[sym['main.' + s['name']]])
        return {'ok': True, 'outputs': outs, 'witness_size': len(w),
                'ms': int((time.time() - t0) * 1000)}

    def test(self, pkg, d, timeout=1200):
        """Each tests/*.json is one case: expect outputs, or fail=true."""
        cases = self.case_tests(pkg, d)
        r = self.merge_tests({}, cases)
        if not cases:
            r['log'] = 'no test cases in tests/*.json'
        return r

    def rename(self, d, old, new):
        m = os.path.join(d, 'Circom.toml')
        if os.path.exists(m):
            _emit(m, re.sub(r'(?m)^name\s*=\s*"[^"]*"', f'name = "{new}"', _read(m) or '', count=1))


def _norm(v):
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return None if v is None else str(v)


_ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')


def _ansi(log):
    return _ANSI.sub('', log or '').strip()


def _toml(v):
    """One Prover.toml value: Fields as quoted strings, arrays of them, structs inline."""
    if isinstance(v, dict):
        return '{ ' + ', '.join(f'{k} = {_toml(x)}' for k, x in v.items()) + ' }'
    if isinstance(v, (list, tuple)):
        return '[' + ', '.join(_toml(x) for x in v) + ']'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    return json.dumps(str(v).strip().strip('\'"'))


def _dec(s):
    """nargo prints outputs as hex Fields (0x2a, [0x1, 0x2]); hand back decimals."""
    s = s.strip()
    if s.startswith('[') and s.endswith(']'):
        return [_dec(x) for x in s[1:-1].split(',') if x.strip()]
    try:
        return str(int(s, 16) if s.lower().startswith('0x') else int(s))
    except ValueError:
        return s


# ---------------------------------------------------------------------------
# Noir — ACIR circuits via nargo (Aztec); bb proves
# ---------------------------------------------------------------------------

_NARGO_TEST = re.compile(r'^\[[^\]]+\] Testing (\S+)\s*\.\.\. (ok|FAIL)', re.M)
_NOIR_DIAG = re.compile(r'^(error|warning): (.+?)\n\s*┌─ ([^:\n]+):(\d+):(\d+)', re.M)


class Noir(Framework):
    id = 'noir'
    title = 'Noir'
    lang = 'Noir'
    blurb = ('A Rust-like language for ZK programs (Aztec). nargo compiles to '
             'ACIR and runs #[test] fns; any backend (Barretenberg bb) proves.')
    marker = 'Nargo.toml'
    ext = '.nr'
    tools = ('nargo',)
    install = 'curl -L https://raw.githubusercontent.com/noir-lang/noirup/main/install | bash && noirup'
    homepage = 'https://noir-lang.org'
    default_template = 'square'
    layout = {'Nargo.toml.tmpl': 'Nargo.toml',
              'main.nr.tmpl': 'src/main.nr',
              'Prover.toml.tmpl': 'Prover.toml',
              'test.json.tmpl': 'tests/basic.json'}
    prompt = (
        "You are a zero-knowledge engineer working inside a single Noir "
        "project (the current working directory, a nargo package).\n"
        "Rules:\n"
        "- src/main.nr holds `fn main(...)`; public params are marked `pub`. "
        "Tests are `#[test] fn test_x()` (and `#[test(should_fail)]`) in the "
        "same crate.\n"
        "- ALWAYS run `nargo compile` and `nargo test` before you finish; fix "
        "every error you introduce. Prover.toml holds sample inputs for "
        "`nargo execute`.\n"
        "- Nargo.toml's package name is the directory name; keep it.\n"
    )

    def sources(self, d):
        return [os.path.join(d, 'Nargo.toml')] + glob.glob(
            os.path.join(d, 'src', '**', '*.nr'), recursive=True)

    def tests(self, d):
        return [p for p in self.sources(d)
                if p.endswith('.nr') and '#[test' in (_read(p) or '')]

    def artifacts(self, d):
        return glob.glob(os.path.join(d, 'target', '*.json'))

    def build(self, pkg, d, timeout=900):
        r = run('nargo', ['compile'], d, timeout)
        r['diagnostics'] = diagnostics(r.get('log'), d, _NOIR_DIAG)
        if r.get('ok'):
            r['artifacts'] = self.abi(pkg, d).get('circuits', [])
        return r

    def test(self, pkg, d, timeout=1200):
        r = run('nargo', ['test'], d, timeout, clean=_ansi)
        tests = [{'status': 'pass' if m.group(2) == 'ok' else 'fail',
                  'name': m.group(1), 'gas': None}
                 for m in _NARGO_TEST.finditer(r.get('log') or '')]
        r.update(tests=tests, diagnostics=diagnostics(r.get('log'), d, _NOIR_DIAG))
        return self.merge_tests(r, self.case_tests(pkg, d))

    def witness(self, pkg, d, inputs):
        """nargo execute with the inputs as a throwaway prover file; the
        return value (if main has one) comes back as `return`."""
        if self.build_state(d) == 'unbuilt':
            return {'ok': False, 'error': 'build it first'}
        prover = f'.zkw_{os.getpid()}_{int(time.time() * 1000)}'
        path = os.path.join(d, prover + '.toml')
        _emit(path, ''.join(f'{k} = {_toml(v)}\n' for k, v in (inputs or {}).items()))
        t0 = time.time()
        try:
            r = run('nargo', ['execute', '--prover-name', prover, '--silence-warnings'],
                    d, 300, clean=_ansi)
        finally:
            os.path.exists(path) and os.remove(path)
        log = r.get('log') or r.get('error') or ''
        if not r.get('ok'):
            why = re.search(r"Failed to solve program: '([^']*)'", log) or \
                re.search(r'error: (.*)', log)
            at = re.search(r'at (src/[^:\s]+):(\d+)', log)
            msg = why.group(1) if why else log[-600:]
            if at:
                msg += f' ({at.group(1)}, line {at.group(2)})'
            return {'ok': False, 'error': msg, 'line': int(at.group(2)) if at else None,
                    'log': log[-3000:]}
        out = re.search(r'Circuit output: (.+)', log)
        outs = {'return': _dec(out.group(1).strip())} if out else {}
        return {'ok': True, 'outputs': outs, 'ms': int((time.time() - t0) * 1000)}

    def abi(self, pkg, d):
        out = []
        for p in sorted(self.artifacts(d)):
            try:
                art = json.load(open(p))
            except Exception:  # noqa: BLE001
                continue
            abi = art.get('abi') or {}
            ins = [{'name': x.get('name'), 'type': (x.get('type') or {}).get('kind'),
                    'public': x.get('visibility') == 'public'}
                   for x in abi.get('parameters', [])]
            ret = abi.get('return_type')
            out.append({'circuit': os.path.splitext(os.path.basename(p))[0],
                        'inputs': ins,
                        'outputs': [{'name': 'return', 'type': ((ret or {}).get('abi_type') or {}).get('kind')}] if ret else [],
                        'constraints': None, 'artifact': os.path.relpath(p, d)})
        return {'name': pkg, 'state': self.build_state(d), 'contracts': [],
                'circuits': out, **({} if out else {'error': 'not built yet'})}


# ---------------------------------------------------------------------------
# value helpers shared by the executors below
# ---------------------------------------------------------------------------

_ANSI = re.compile(r'\x1b\[[0-9;]*m')


def _ansi(log):
    return _ANSI.sub('', log or '').strip()


def _toml(v):
    if isinstance(v, list):
        return '[' + ', '.join(_toml(x) for x in v) + ']'
    return json.dumps(str(v))


def _dec(v):
    """0x21 -> '33', [..] elementwise, anything else unchanged."""
    v = v.strip().strip('"')
    if v.startswith('[') and v.endswith(']'):
        return [_dec(x) for x in v[1:-1].split(',') if x.strip()]
    return str(int(v, 16)) if re.fullmatch(r'0x[0-9a-fA-F]+', v) else v


def _shell(cmd, cwd, timeout, stdin=None, extra_env=None):
    """Run argv or a shell string; same result shape as run()."""
    e = env()
    e.update(extra_env or {})
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, input=stdin,
                             timeout=timeout, env=e, shell=isinstance(cmd, str))
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'timed out after {timeout}s'}
    except FileNotFoundError as ex:
        return {'ok': False, 'error': f'not installed: {ex.filename}'}
    return {'ok': out.returncode == 0, 'code': out.returncode,
            'stdout': out.stdout, 'log': _ansi(out.stdout + out.stderr)[-12000:]}


# ---------------------------------------------------------------------------
# ZoKrates — a Python-like DSL to R1CS (bn128 default), witness by compute-witness
# ---------------------------------------------------------------------------

class ZoKrates(Framework):
    id = 'zokrates'
    title = 'ZoKrates'
    lang = 'ZoKrates DSL'
    blurb = ('A high-level, Python-like language for zkSNARKs on Ethereum. '
             'zokrates compiles to R1CS + an ABI, computes witnesses and '
             'exports a Solidity verifier. Tests are the shared JSON cases.')
    marker = 'main.zok'
    ext = '.zok'
    tools = ('zokrates',)
    install = 'curl -LSfs get.zokrat.es | sh  (binary + stdlib in ~/.zokrates)'
    homepage = 'https://zokrates.github.io'
    proof = 'Groth16 · GM17 · Marlin'
    default_template = 'factors'
    layout = {'main.zok.tmpl': 'main.zok', 'test.json.tmpl': 'tests/basic.json',
              'zokrates.toml.tmpl': 'zokrates.toml'}
    prompt = (
        "You are a zero-knowledge engineer working inside a single ZoKrates "
        "project (the current working directory).\n"
        "Rules:\n"
        "- main.zok holds `def main(private field a, field b) -> field { ... }`; "
        "parameters are public unless marked `private`. Helpers go in other "
        ".zok files, `from \"./lib.zok\" import f;`. Enforce with assert().\n"
        "- Tests are JSON files in tests/: [{\"name\", \"input\": {param: "
        "value}, \"expect\": {\"out\": value}} | {\"input\": ..., \"fail\": "
        "true}]. Values are decimal strings. The return value is `out`.\n"
        "- Build: `zokrates compile -i main.zok -o target/out -s "
        "target/abi.json -r target/out.r1cs`. Run: `zokrates compute-witness "
        "-i target/out -s target/abi.json --abi --stdin --verbose` with a JSON "
        "array of the arguments. ALWAYS build and run every case.\n"
    )

    def sources(self, d):
        return [x for x in glob.glob(os.path.join(d, '**', '*.zok'), recursive=True)
                if '/target/' not in x]

    def tests(self, d):
        return self.case_files(d)

    def artifacts(self, d):
        arts = [os.path.join(d, 'target', 'out'), os.path.join(d, 'target', 'abi.json')]
        return arts if all(os.path.exists(a) for a in arts) else []

    def build(self, pkg, d, timeout=900):
        os.makedirs(os.path.join(d, 'target'), exist_ok=True)
        r = run('zokrates', ['compile', '-i', 'main.zok', '-o', 'target/out',
                             '-s', 'target/abi.json', '-r', 'target/out.r1cs'], d, timeout)
        r['diagnostics'] = diagnostics(r.get('log'), d, _ZOK_DIAG)
        if r.get('ok'):
            n = re.search(r'Number of constraints: (\d+)', r.get('log') or '')
            _emit(os.path.join(d, 'target', 'info.json'),
                  json.dumps({'constraints': int(n.group(1)) if n else None}))
            r['artifacts'] = self.abi(pkg, d).get('circuits', [])
        return r

    def abi(self, pkg, d):
        try:
            spec = json.load(open(os.path.join(d, 'target', 'abi.json')))
        except Exception:  # noqa: BLE001
            return {'name': pkg, 'state': self.build_state(d), 'contracts': [],
                    'circuits': [], 'error': 'not built yet'}
        try:
            info = json.load(open(os.path.join(d, 'target', 'info.json')))
        except Exception:  # noqa: BLE001
            info = {}
        outs = spec.get('outputs') or ([spec['output']] if spec.get('output') else [])
        # `return;` is an empty tuple: no outputs; a tuple return is one each
        if len(outs) == 1 and outs[0].get('type') == 'tuple':
            outs = (outs[0].get('components') or {}).get('elements') or []
        return {'name': pkg, 'state': self.build_state(d), 'contracts': [],
                'circuits': [{'circuit': 'main',
                              'inputs': [{'name': i['name'], 'type': i.get('type'),
                                          'public': bool(i.get('public')),
                                          'dims': [str(i['components']['size'])]
                                          if i.get('type') == 'array' else []}
                                         for i in spec.get('inputs', [])],
                              'outputs': [{'name': 'out' if len(outs) == 1 else f'out{k}',
                                           'type': o.get('type'), 'dims': []}
                                          for k, o in enumerate(outs)],
                              'constraints': info.get('constraints'), 'info': info,
                              'artifact': 'target/out'}]}

    def witness(self, pkg, d, inputs):
        if self.build_state(d) == 'unbuilt':
            return {'ok': False, 'error': 'build it first'}
        c = (self.abi(pkg, d).get('circuits') or [{}])[0]
        args = []
        for i in c.get('inputs', []):
            if i['name'] not in (inputs or {}):
                return {'ok': False, 'error': f"missing input {i['name']}"}
            v = inputs[i['name']]
            if i.get('type') == 'bool':
                v = str(v).lower() in ('1', 'true')
            elif isinstance(v, list):
                v = [str(x) for x in v]
            else:
                v = str(v)
            args.append(v)
        t0 = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            r = _shell([tool('zokrates') or 'zokrates', 'compute-witness', '-i', 'target/out',
                        '-s', 'target/abi.json', '-o', os.path.join(tmp, 'w'),
                        '--circom-witness', os.path.join(tmp, 'w.wtns'),
                        '--abi', '--stdin', '--verbose'], d, 120, stdin=json.dumps(args))
        log = r.get('log') or r.get('error') or ''
        if not r.get('ok') or 'Execution failed' in log:
            why = re.search(r'Execution failed: (.*)', log) or re.search(r'(?:Error|error):? (.*)', log)
            at = re.search(r'main\.zok:(\d+)', log)
            return {'ok': False, 'error': (why.group(1) if why else log[-600:]).strip(),
                    'line': int(at.group(1)) if at else None, 'log': log[-3000:]}
        m = re.search(r'Witness:\s*\n(.+)', log)
        vals = []
        if m:
            try:
                vals = json.loads(m.group(1))
            except Exception:  # noqa: BLE001
                vals = [m.group(1).strip()]
        vals = vals if isinstance(vals, list) else [vals]
        names = [o['name'] for o in c.get('outputs', [])]
        outs = {n: (v if not isinstance(v, list) else [str(x) for x in v]) if isinstance(v, list) else str(v)
                for n, v in zip(names, vals)}
        return {'ok': True, 'outputs': outs, 'ms': int((time.time() - t0) * 1000)}

    def test(self, pkg, d, timeout=1200):
        cases = self.case_tests(pkg, d)
        r = self.merge_tests({}, cases)
        if not cases:
            r['log'] = 'no test cases in tests/*.json'
        return r

    def rename(self, d, old, new):
        m = os.path.join(d, 'zokrates.toml')
        if os.path.exists(m):
            _emit(m, re.sub(r'(?m)^name\s*=\s*"[^"]*"', f'name = "{new}"', _read(m) or '', count=1))


_ZOK_DIAG = re.compile(r'^(?:(error|warning)[: ]+)?(?P<msg>)([^\s:]+\.zok):(\d+):(\d+)', re.M)


# ---------------------------------------------------------------------------
# Leo — Aleo's private-by-default language; leo run executes off-chain
# ---------------------------------------------------------------------------

_LEO_SIG = re.compile(r'\bfn\s+main\s*\(([^)]*)\)\s*(?:->\s*([^{]+))?\{')


class Leo(Framework):
    id = 'leo'
    title = 'Leo · Aleo'
    lang = 'Leo'
    blurb = ('A Rust-like, private-by-default language for Aleo programs. '
             'leo builds Aleo instructions, `leo run` executes off-chain and '
             '@test fns run in-VM. Shared JSON cases run through `leo run`.')
    marker = 'program.json'
    needle = '.aleo'
    ext = '.leo'
    tools = ('leo',)
    install = 'download leo-lang-<v>-x86_64-unknown-linux-musl.zip from github.com/ProvableHQ/leo releases into ~/.local/bin (or cargo install leo-lang)'
    homepage = 'https://leo-lang.org'
    proof = 'Varuna (Marlin-family)'
    default_template = 'factors'
    layout = {'program.json.tmpl': 'program.json', 'main.leo.tmpl': 'src/main.leo',
              'test.leo.tmpl': 'tests/test_{PKG}.leo', 'test.json.tmpl': 'tests/basic.json'}
    prompt = (
        "You are a zero-knowledge engineer working inside a single Leo (Aleo) "
        "program (the current working directory).\n"
        "Rules:\n"
        "- src/main.leo is `program <pkg>.aleo { @noupgrade constructor() {} "
        "fn main(public c: field, a: field, b: field) -> field { ... } }`; "
        "inputs are private unless marked `public`. Enforce with assert / "
        "assert_eq / assert_neq.\n"
        "- Native tests live in tests/test_<pkg>.leo (`@test fn ...`, "
        "`@should_fail`). Shared cases are tests/*.json: [{\"name\", \"input\": "
        "{param: value}, \"expect\": {\"return\": value}} | {\"input\": ..., "
        "\"fail\": true}] with plain decimal values (no type suffix).\n"
        "- ALWAYS run `leo build --offline` and `leo test --offline`, and `leo "
        "run --offline main <args with suffix, e.g. 3field>` for each case.\n"
        "- program.json's program name is <pkg>.aleo; keep it.\n"
    )

    def sources(self, d):
        return [os.path.join(d, 'program.json')] + [
            x for x in glob.glob(os.path.join(d, '**', '*.leo'), recursive=True)
            if '/build/' not in x]

    def artifacts(self, d):
        return glob.glob(os.path.join(d, 'build', '*', 'abi.json'))[:1]

    clean_dirs = ('build', 'outputs')

    def build(self, pkg, d, timeout=900):
        r = run('leo', ['build', '--offline', '--disable-update-check'], d, timeout, clean=_ansi)
        r['diagnostics'] = diagnostics(r.get('log'), d, _LEO_DIAG)
        if r.get('ok'):
            r['artifacts'] = self.abi(pkg, d).get('circuits', [])
        return r

    def _sig(self, d):
        src = _read(os.path.join(d, 'src', 'main.leo')) or ''
        m = _LEO_SIG.search(src)
        if not m:
            return [], []
        ins = []
        for p in [x.strip() for x in m.group(1).split(',') if x.strip()]:
            mm = re.match(r'(public|private|constant)?\s*(\w+)\s*:\s*(.+)', p)
            if mm:
                ins.append({'name': mm.group(2), 'type': mm.group(3).strip(),
                            'public': mm.group(1) == 'public', 'dims': []})
        ret = (m.group(2) or '').strip().strip('()')
        rets = [x.strip() for x in ret.split(',') if x.strip()]
        outs = [{'name': 'return' if len(rets) == 1 else f'return{k}', 'type': t, 'dims': []}
                for k, t in enumerate(rets)]
        return ins, outs

    def abi(self, pkg, d):
        ins, outs = self._sig(d)
        if not ins and not outs:
            return {'name': pkg, 'contracts': [], 'circuits': [],
                    'error': 'no `fn main(...)` in src/main.leo'}
        return {'name': pkg, 'state': self.build_state(d), 'contracts': [],
                'circuits': [{'circuit': f'{pkg}.aleo/main', 'inputs': ins,
                              'outputs': outs, 'constraints': None,
                              'artifact': f'build/{pkg}/{pkg}.aleo'}]}

    def witness(self, pkg, d, inputs):
        if self.build_state(d) == 'unbuilt':
            return {'ok': False, 'error': 'build it first'}
        ins, outs = self._sig(d)
        args = []
        for i in ins:
            if i['name'] not in (inputs or {}):
                return {'ok': False, 'error': f"missing input {i['name']}"}
            v, ty = str(inputs[i['name']]).strip(), i['type']
            args.append(v if ty in ('bool', 'address') or v.endswith(ty) else v + ty)
        t0 = time.time()
        r = run('leo', ['run', '--offline', '--disable-update-check', '-q', '--json-output',
                        'main', *args], d, 300, clean=_ansi)
        log = r.get('log') or r.get('error') or ''
        if not r.get('ok'):
            why = re.search(r"Error \[\w+\]: (.*)", log) or re.search(r'error: (.*)', log)
            msg = (why.group(1) if why else log[-600:]).strip()
            fail = re.search(r"'(assert[^']*)' failed[^\n]*", msg)
            return {'ok': False, 'error': fail.group(0) if fail else msg, 'log': log[-3000:]}
        try:
            vals = json.load(open(os.path.join(d, 'build', 'json-outputs', 'run.json'))).get('outputs', [])
        except Exception:  # noqa: BLE001
            vals = re.findall(r'^\s*•\s*(\S+)', log, re.M)
        strip = lambda v: re.sub(r'(field|group|scalar|[ui](8|16|32|64|128))$', '', str(v))  # noqa: E731
        return {'ok': True, 'outputs': {o['name']: strip(v) for o, v in zip(outs, vals)},
                'ms': int((time.time() - t0) * 1000)}

    def test(self, pkg, d, timeout=1200):
        r = run('leo', ['test', '--offline', '--disable-update-check'], d, timeout, clean=_ansi)
        r['tests'] = [{'status': m.group(1).lower(), 'name': m.group(2), 'gas': None}
                      for m in re.finditer(r'^\s*(PASS|FAIL)\s+(\S+)', r.get('log') or '', re.M)]
        return self.merge_tests(r, self.case_tests(pkg, d))

    def rename(self, d, old, new):
        for p in self.sources(d):
            _emit(p, re.sub(r'\b' + re.escape(old) + r'\.aleo\b', f'{new}.aleo', _read(p) or ''))
        for p in glob.glob(os.path.join(d, 'tests', f'test_{old}.leo')):
            body = re.sub(r'\btest_' + re.escape(old) + r'\b', f'test_{new}', _read(p) or '')
            os.remove(p)
            _emit(os.path.join(d, 'tests', f'test_{new}.leo'), body)


_LEO_DIAG = re.compile(r'^(Error|Warning) \[\w+\]: (.+?)\n\s*--> ([^:\n]+):(\d+):(\d+)', re.M)


# ---------------------------------------------------------------------------
# The runner protocol: any framework whose project ships a small runner
# ---------------------------------------------------------------------------
#
# Libraries (gnark, halo2, arkworks, ...) and zkVMs (RISC Zero, SP1, ...) have
# no single CLI that "runs the circuit on these inputs". So zkworld asks the
# PROJECT for one, with a two-call contract:
#
#     <runner> --abi          stdout: {"circuit", "inputs": [{name, public,
#                              type?, dims?}], "outputs": [{name}],
#                              "constraints"?}
#     <runner> <input.json>   exit 0 + stdout last line {"outputs": {..}}
#                              = accepted; exit != 0 = rejected, the last
#                              stderr line says why
#
# The Runner adapter builds the project, caches the --abi answer in
# target/zkworld.abi.json, and drives the shared JSON cases through it.

class Runner(Framework):
    kind = 'library'
    build_steps = ()         # argv lists, run in order
    test_cmd = None          # the framework's native test runner (argv), optional
    run_env = {}

    def runner(self, pkg, d):
        """argv of the built runner binary."""
        raise NotImplementedError

    def env_for(self, d):
        return dict(self.run_env)

    def test_for(self, d):
        return list(self.test_cmd) if self.test_cmd else None

    def artifacts(self, d):
        p = os.path.join(d, 'target', 'zkworld.abi.json')
        return [p] if os.path.exists(p) else []

    def _steps(self, pkg, d):
        return [list(s) for s in self.build_steps]

    def build(self, pkg, d, timeout=900):
        os.makedirs(os.path.join(d, 'target'), exist_ok=True)
        logs = []
        for step in self._steps(pkg, d):
            r = _shell(step, d, timeout, extra_env=self.env_for(d))
            logs.append(r.get('log') or r.get('error') or '')
            if not r.get('ok'):
                return {'ok': False, 'log': '\n'.join(logs)[-12000:],
                        'error': r.get('error'), 'diagnostics': self.diag(r.get('log'), d)}
        r = _shell(self.runner(pkg, d) + ['--abi'], d, 300, extra_env=self.env_for(d))
        try:
            abi = json.loads((r.get('stdout') or '').strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            return {'ok': False, 'log': '\n'.join(logs + [r.get('log') or '']),
                    'error': 'runner --abi did not print JSON', 'diagnostics': []}
        _emit(os.path.join(d, 'target', 'zkworld.abi.json'), json.dumps(abi, indent=2))
        return {'ok': True, 'log': '\n'.join(logs).strip()[-12000:], 'diagnostics': [],
                'artifacts': self.abi(pkg, d).get('circuits', [])}

    def diag(self, log, d):
        return []

    def abi(self, pkg, d):
        try:
            a = json.load(open(os.path.join(d, 'target', 'zkworld.abi.json')))
        except Exception:  # noqa: BLE001
            return {'name': pkg, 'contracts': [], 'circuits': [], 'error': 'not built yet'}
        c = {'circuit': a.get('circuit') or pkg,
             'inputs': [{'dims': [], **i} for i in a.get('inputs', [])],
             'outputs': [{'dims': [], **o} for o in a.get('outputs', [])],
             'constraints': a.get('constraints'), 'artifact': 'target/zkworld.abi.json'}
        return {'name': pkg, 'state': self.build_state(d), 'contracts': [], 'circuits': [c]}

    def witness(self, pkg, d, inputs):
        if self.build_state(d) == 'unbuilt':
            return {'ok': False, 'error': 'build it first'}
        t0 = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, 'input.json')
            _emit(p, json.dumps({k: (v if isinstance(v, list) else str(v))
                                 for k, v in (inputs or {}).items()}))
            r = _shell(self.runner(pkg, d) + [p], d, 300, extra_env=self.env_for(d))
        lines = (r.get('stdout') or '').strip().splitlines()
        if not r.get('ok'):
            log = r.get('log') or r.get('error') or ''
            return {'ok': False, 'error': (log.strip().splitlines() or ['rejected'])[-1][:600],
                    'log': log[-3000:]}
        try:
            outs = json.loads(lines[-1]).get('outputs') or {}
        except Exception:  # noqa: BLE001
            outs = {}
        return {'ok': True, 'outputs': {k: v if isinstance(v, list) else str(v)
                                        for k, v in outs.items()},
                'ms': int((time.time() - t0) * 1000)}

    def native_tests(self, log):
        return []

    def test(self, pkg, d, timeout=1200):
        native = {}
        cmd = self.test_for(d)
        if cmd:
            native = _shell(cmd, d, timeout, extra_env=self.env_for(d))
            native['tests'] = self.native_tests(native.get('log'))
            if not native['tests'] and not native.get('ok'):
                native['tests'] = [{'status': 'fail', 'name': 'native', 'gas': None,
                                    'reason': (native.get('log') or native.get('error') or '')[-300:]}]
            native.pop('stdout', None)
        return self.merge_tests(native, self.case_tests(pkg, d))


class Gnark(Runner):
    id = 'gnark'
    title = 'gnark'
    lang = 'Go'
    blurb = ('Consensys\' Go library for zkSNARK circuits: a Define(api) '
             'method over frontend.Variable fields. The starter ships a runner '
             '(zkworld.go) that solves the circuit with test.IsSolved; `go test` '
             'runs the native asserts.')
    marker = 'go.mod'
    needle = 'consensys/gnark'
    ext = '.go'
    tools = ('go',)
    install = 'go 1.22+ (here /usr/local/go); gnark itself is a go module fetched on build'
    homepage = 'https://docs.gnark.consensys.io'
    proof = 'Groth16 · PLONK (BN254, BLS12-381, ...)'
    default_template = 'factors'
    layout = {'go.mod.tmpl': 'go.mod', 'circuit.go.tmpl': 'circuit.go',
              'circuit_test.go.tmpl': 'circuit_test.go', 'zkworld.go.tmpl': 'zkworld.go',
              'test.json.tmpl': 'tests/basic.json'}
    build_steps = (('go', 'mod', 'tidy'), ('go', 'build', '-o', 'target/runner', '.'))
    test_cmd = ('go', 'test', '-v', './...')
    prompt = (
        "You are a zero-knowledge engineer working inside a single gnark "
        "(Go) project (the current working directory).\n"
        "Rules:\n"
        "- circuit.go defines `type Circuit struct` with frontend.Variable "
        "fields tagged `gnark:\"name\"` (secret) or `gnark:\"name,public\"`, and "
        "`Define(api frontend.API) error`. Constrain with api.AssertIsEqual, "
        "AssertIsDifferent, AssertIsLessOrEqual, ... Comment WHY.\n"
        "- zkworld.go is the runner (`--abi`, `<input.json>`); keep it working "
        "and only touch it if the circuit gains outputs.\n"
        "- circuit_test.go uses test.NewAssert(t).ProverSucceeded/ProverFailed.\n"
        "- Shared cases: tests/*.json [{\"name\", \"input\": {tag: value}} | "
        "{..., \"fail\": true}], decimal strings.\n"
        "- ALWAYS run `go build -o target/runner . && go test ./...` and "
        "`target/runner tests-input.json` before you finish.\n"
    )

    def sources(self, d):
        return [os.path.join(d, 'go.mod')] + [x for x in glob.glob(os.path.join(d, '*.go'))]

    def runner(self, pkg, d):
        return [os.path.join(d, 'target', 'runner')]

    def native_tests(self, log):
        return [{'status': 'pass' if m.group(1) == 'PASS' else 'fail', 'name': m.group(2), 'gas': None}
                for m in re.finditer(r'^--- (PASS|FAIL): (\S+)', log or '', re.M)]

    def diag(self, log, d):
        # ./circuit.go:14:2: undefined: x
        return [{'level': 'error', 'message': m.group(4).strip(), 'path': m.group(1),
                 'line': int(m.group(2)), 'col': int(m.group(3))}
                for m in re.finditer(r'^\.?/?([\w./-]+\.go):(\d+):(\d+): (.+)$', log or '', re.M)]

    def rename(self, d, old, new):
        m = os.path.join(d, 'go.mod')
        _emit(m, re.sub(r'(?m)^module\s+\S+', f'module zkworld/{new}', _read(m) or '', count=1))


class Halo2(Runner):
    id = 'halo2'
    title = 'halo2'
    lang = 'Rust'
    blurb = ('PLONKish circuits in Rust (zcash halo2_proofs): columns, gates, '
             'regions. The starter\'s binary is the runner and checks the '
             'circuit with MockProver; `cargo test` runs the native tests.')
    marker = 'Cargo.toml'
    needle = 'halo2_proofs'
    ext = '.rs'
    tools = ('cargo',)
    install = 'rustup (cargo); halo2_proofs is fetched from crates.io on build'
    homepage = 'https://zcash.github.io/halo2'
    proof = 'Halo2 (PLONKish, IPA over Pasta)'
    default_template = 'factors'
    layout = {'Cargo.toml.tmpl': 'Cargo.toml', 'main.rs.tmpl': 'src/main.rs',
              'test.json.tmpl': 'tests/basic.json'}
    build_steps = (('cargo', 'build', '--release', '-q'),)
    test_cmd = ('cargo', 'test', '--release')
    prompt = (
        "You are a zero-knowledge engineer working inside a single halo2 "
        "(Rust, halo2_proofs 0.3) crate (the current working directory).\n"
        "Rules:\n"
        "- src/main.rs holds the Circuit (configure: columns + gates; "
        "synthesize: assign regions, constrain_instance for public values) "
        "AND the zkworld runner in main(): `--abi` prints the shape, "
        "`<input.json>` runs MockProver and exits 1 on failure. Keep both.\n"
        "- Native tests are #[cfg(test)] fns using MockProver::run(..).verify().\n"
        "- Shared cases: tests/*.json [{\"name\", \"input\": {name: value}} | "
        "{..., \"fail\": true}], decimal strings.\n"
        "- ALWAYS run `cargo build --release` and `cargo test --release`.\n"
    )

    def sources(self, d):
        return [os.path.join(d, 'Cargo.toml')] + glob.glob(os.path.join(d, 'src', '**', '*.rs'), recursive=True)

    def runner(self, pkg, d):
        return [os.path.join(d, 'target', 'release', pkg)]

    def native_tests(self, log):
        return [{'status': 'pass' if m.group(2) == 'ok' else 'fail', 'name': m.group(1), 'gas': None}
                for m in re.finditer(r'^test (\S+) \.\.\. (ok|FAILED)', log or '', re.M)]

    def diag(self, log, d):
        return diagnostics(log, d)

    def rename(self, d, old, new):
        m = os.path.join(d, 'Cargo.toml')
        _emit(m, re.sub(r'(?m)^name\s*=\s*"[^"]*"', f'name = "{new}"', _read(m) or '', count=1))


class Custom(Runner):
    """Any toolchain at all: the project's config.json says how.

        "zk": {"lang": "Rust · RISC Zero", "build": ["cargo build --release"],
               "test": "cargo test", "run": "target/release/host",
               "env": {"RISC0_DEV_MODE": "1"}}

    `run` must speak the runner protocol (--abi / <input.json>). This is how a
    RISC Zero, SP1, Jolt, arkworks, o1js, ... project joins zkworld without
    zkworld learning about it: CATALOG carries a preset for each."""
    id = 'custom'
    title = 'Any framework (runner protocol)'
    lang = 'any'
    blurb = ('Bring any ZK toolchain: the project\'s config.json `zk` block names '
             'its build/test/run commands, and its runner speaks the two-call '
             'protocol (--abi, <input.json>). zkVMs and libraries plug in here.')
    marker = 'config.json'
    needle = '"zk"'
    ext = ''
    tools = ()
    homepage = 'https://github.com/'
    proof = 'whatever the project uses'
    kind = 'protocol'
    prompt = (
        "You are a zero-knowledge engineer working inside a single project "
        "(the current working directory) whose ZK framework zkworld drives "
        "through the RUNNER PROTOCOL.\n"
        "Rules:\n"
        "- config.json `zk` names the commands: build (list of shell "
        "commands), test (optional), run, env. Keep it accurate; change it if "
        "the layout you choose needs different commands.\n"
        "- `<run> --abi` must print ONE JSON line: {\"circuit\": name, "
        "\"inputs\": [{\"name\", \"public\": bool}], \"outputs\": "
        "[{\"name\"}]}.\n"
        "- `<run> input.json` ({name: decimal string}) must execute the "
        "program/circuit (dev/mock proving is fine) and exit 0 printing "
        "{\"outputs\": {...}} as the last stdout line when the statement "
        "holds, or exit non-zero with the reason as the last stderr line.\n"
        "- tests/*.json are the statement cases: [{name, input, expect?} | "
        "{..., fail: true}]. ALWAYS run the build commands, then the runner "
        "on every case, before you finish. Install a missing toolchain only "
        "under the user's home dir.\n"
    )

    def detect(self, d):
        return bool(self._zk(d).get('run'))

    @staticmethod
    def _zk(d):
        try:
            z = json.load(open(os.path.join(d, 'config.json'))).get('zk')
            return z if isinstance(z, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def sources(self, d):
        return [x for x in glob.glob(os.path.join(d, '**', '*'), recursive=True)
                if os.path.isfile(x) and '/target/' not in x and '/node_modules/' not in x
                and '/tests/' not in x][:2000]

    def _steps(self, pkg, d):
        b = self._zk(d).get('build') or []
        return [b] if isinstance(b, str) else list(b)

    def runner(self, pkg, d):
        r = self._zk(d).get('run')
        r = r.split() if isinstance(r, str) else list(r)
        # a relative runner path is the project's own binary
        if r and not os.path.isabs(r[0]) and os.sep in r[0]:
            r[0] = os.path.join(d, r[0])
        return r

    def env_for(self, d):
        return {k: str(v) for k, v in (self._zk(d).get('env') or {}).items()}

    def test_for(self, d):
        return self._zk(d).get('test') or None

    def rename(self, d, old, new):
        pass



# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

# order matters for detect(): specific markers first, Custom (config.json) last
FRAMEWORKS = {f.id: f for f in (Cairo(), Circom(), Noir(), ZoKrates(), Leo(),
                                 Gnark(), Halo2(), Custom())}
DEFAULT = 'cairo'


# ---------------------------------------------------------------------------
# the catalog: every ZK framework worth knowing, driven or not
# ---------------------------------------------------------------------------
#
# `driven` ones have an adapter above. The rest join through the runner
# protocol (Custom): `preset` is the config.json `zk` block a project of that
# framework would carry, so new(framework=<id>) can scaffold the block and an
# agent fills in the code. kind: dsl (a circuit language), zkvm (prove
# ordinary programs), library (circuits as code in a host language).

CATALOG = [
    # -- circuit languages --
    {'id': 'cairo', 'kind': 'dsl', 'lang': 'Cairo', 'by': 'StarkWare', 'proof': 'STARK (Stwo/Stone)', 'homepage': 'https://www.cairo-lang.org'},
    {'id': 'circom', 'kind': 'dsl', 'lang': 'Circom', 'by': 'iden3', 'proof': 'Groth16 · PLONK · FFLONK (snarkjs)', 'homepage': 'https://docs.circom.io'},
    {'id': 'noir', 'kind': 'dsl', 'lang': 'Noir', 'by': 'Aztec', 'proof': 'UltraHonk (Barretenberg), any ACIR backend', 'homepage': 'https://noir-lang.org'},
    {'id': 'zokrates', 'kind': 'dsl', 'lang': 'ZoKrates', 'by': 'ZoKrates', 'proof': 'Groth16 · GM17 · Marlin', 'homepage': 'https://zokrates.github.io'},
    {'id': 'leo', 'kind': 'dsl', 'lang': 'Leo', 'by': 'Aleo', 'proof': 'Varuna', 'homepage': 'https://leo-lang.org'},
    {'id': 'o1js', 'name': 'o1js', 'kind': 'dsl', 'lang': 'TypeScript', 'by': 'o1Labs (Mina)', 'proof': 'Kimchi (PLONKish, Pickles recursion)', 'homepage': 'https://docs.minaprotocol.com/zkapps/o1js',
     'install': 'npm i o1js', 'preset': {'build': ['npm install', 'npx tsc'], 'run': 'node build/runner.js'}},
    {'id': 'compact', 'name': 'Compact', 'kind': 'dsl', 'lang': 'Compact', 'by': 'Midnight', 'proof': 'PLONK (Halo2-based)', 'homepage': 'https://docs.midnight.network',
     'install': 'Midnight compactc toolchain', 'preset': {'build': ['compactc src/main.compact target'], 'run': 'node runner.mjs'}},
    {'id': 'noname', 'name': 'noname', 'kind': 'dsl', 'lang': 'noname', 'by': 'zksecurity', 'proof': 'Kimchi · R1CS', 'homepage': 'https://github.com/zksecurity/noname',
     'install': 'cargo install --git https://github.com/zksecurity/noname', 'preset': {'build': ['noname build'], 'run': 'python3 runner.py'}},
    {'id': 'lurk', 'name': 'Lurk', 'kind': 'dsl', 'lang': 'Lisp', 'by': 'Argument', 'proof': 'Nova-family folding', 'homepage': 'https://lurk-lang.org',
     'install': 'cargo install lurk', 'preset': {'run': 'python3 runner.py'}},
    # -- zkVMs: prove ordinary programs --
    {'id': 'risc0', 'name': 'RISC Zero', 'kind': 'zkvm', 'lang': 'Rust (RISC-V)', 'by': 'RISC Zero', 'proof': 'STARK -> Groth16 wrap', 'homepage': 'https://dev.risczero.com',
     'install': 'curl -L https://risczero.com/install | bash && rzup install',
     'preset': {'build': ['cargo build --release'], 'test': 'cargo test --release', 'run': 'target/release/host', 'env': {'RISC0_DEV_MODE': '1'}}},
    {'id': 'sp1', 'name': 'SP1', 'kind': 'zkvm', 'lang': 'Rust (RISC-V)', 'by': 'Succinct', 'proof': 'STARK (Plonky3) -> Groth16/PLONK wrap', 'homepage': 'https://docs.succinct.xyz',
     'install': 'curl -L https://sp1up.succinct.xyz | bash && sp1up',
     'preset': {'build': ['cd program && cargo prove build', 'cd script && cargo build --release'], 'run': 'script/target/release/script', 'env': {'SP1_PROVER': 'mock'}}},
    {'id': 'jolt', 'name': 'Jolt', 'kind': 'zkvm', 'lang': 'Rust (RISC-V)', 'by': 'a16z', 'proof': 'Lasso/Jolt lookups (sumcheck)', 'homepage': 'https://jolt.a16zcrypto.com',
     'install': 'cargo install --git https://github.com/a16z/jolt jolt', 'preset': {'build': ['cargo build --release'], 'run': 'target/release/runner'}},
    {'id': 'openvm', 'name': 'OpenVM', 'kind': 'zkvm', 'lang': 'Rust (RISC-V)', 'by': 'Axiom', 'proof': 'STARK (Plonky3)', 'homepage': 'https://book.openvm.dev',
     'install': 'cargo install --git https://github.com/openvm-org/openvm cargo-openvm', 'preset': {'build': ['cargo openvm build'], 'run': 'target/release/runner'}},
    {'id': 'zisk', 'name': 'ZisK', 'kind': 'zkvm', 'lang': 'Rust (RISC-V)', 'by': 'Polygon', 'proof': 'STARK (PIL2)', 'homepage': 'https://0xpolygonhermez.github.io/zisk',
     'install': 'ziskup', 'preset': {'build': ['cargo-zisk build --release'], 'run': 'target/release/runner'}},
    {'id': 'nexus', 'name': 'Nexus zkVM', 'kind': 'zkvm', 'lang': 'Rust (RISC-V)', 'by': 'Nexus', 'proof': 'Stwo (circle STARK)', 'homepage': 'https://docs.nexus.xyz',
     'install': 'cargo install --git https://github.com/nexus-xyz/nexus-zkvm cargo-nexus', 'preset': {'build': ['cargo build --release'], 'run': 'target/release/runner'}},
    {'id': 'miden', 'name': 'Miden VM', 'kind': 'zkvm', 'lang': 'Miden assembly · Rust', 'by': '0xMiden', 'proof': 'STARK (Winterfell)', 'homepage': 'https://0xmiden.github.io/miden-vm',
     'install': 'cargo install miden-vm --features executable', 'preset': {'build': [], 'run': 'python3 runner.py'}},
    {'id': 'zkwasm', 'name': 'zkWasm', 'kind': 'zkvm', 'lang': 'WebAssembly', 'by': 'Delphinus Lab', 'proof': 'Halo2', 'homepage': 'https://github.com/DelphinusLab/zkWasm',
     'install': 'see orbit/zkwasm', 'preset': {'run': 'python3 runner.py'}},
    {'id': 'cairo-vm', 'name': 'Cairo VM', 'kind': 'zkvm', 'lang': 'Cairo (Sierra/CASM)', 'by': 'StarkWare · LambdaClass', 'proof': 'STARK (Stwo)', 'homepage': 'https://github.com/lambdaclass/cairo-vm',
     'install': 'scarb (already here) + cairo-run', 'preset': {'build': ['scarb build'], 'run': 'python3 runner.py'}},
    # -- libraries: circuits as code --
    {'id': 'gnark', 'kind': 'library', 'lang': 'Go', 'by': 'Consensys', 'proof': 'Groth16 · PLONK', 'homepage': 'https://docs.gnark.consensys.io'},
    {'id': 'halo2', 'kind': 'library', 'lang': 'Rust', 'by': 'zcash · PSE · Axiom', 'proof': 'Halo2 (PLONKish)', 'homepage': 'https://zcash.github.io/halo2'},
    {'id': 'arkworks', 'name': 'arkworks', 'kind': 'library', 'lang': 'Rust', 'by': 'arkworks', 'proof': 'Groth16 · Marlin · GM17 (R1CS)', 'homepage': 'https://arkworks.rs',
     'install': 'cargo (ark-relations, ark-r1cs-std, ark-groth16 from crates.io)', 'preset': {'build': ['cargo build --release -q'], 'test': 'cargo test --release -q', 'run': 'target/release/runner'}},
    {'id': 'bellman', 'name': 'bellman', 'kind': 'library', 'lang': 'Rust', 'by': 'zcash', 'proof': 'Groth16 (BLS12-381)', 'homepage': 'https://github.com/zkcrypto/bellman',
     'install': 'cargo', 'preset': {'build': ['cargo build --release -q'], 'test': 'cargo test --release -q', 'run': 'target/release/runner'}},
    {'id': 'plonky2', 'name': 'Plonky2', 'kind': 'library', 'lang': 'Rust', 'by': 'Polygon Zero', 'proof': 'PLONK + FRI (Goldilocks)', 'homepage': 'https://github.com/0xPolygonZero/plonky2',
     'install': 'cargo (nightly)', 'preset': {'build': ['cargo build --release -q'], 'test': 'cargo test --release -q', 'run': 'target/release/runner'}},
    {'id': 'plonky3', 'name': 'Plonky3', 'kind': 'library', 'lang': 'Rust', 'by': 'Polygon', 'proof': 'STARK toolkit (AIR + FRI)', 'homepage': 'https://github.com/Plonky3/Plonky3',
     'install': 'cargo', 'preset': {'build': ['cargo build --release -q'], 'test': 'cargo test --release -q', 'run': 'target/release/runner'}},
    {'id': 'stwo', 'name': 'Stwo', 'kind': 'library', 'lang': 'Rust', 'by': 'StarkWare', 'proof': 'Circle STARK (M31)', 'homepage': 'https://github.com/starkware-libs/stwo',
     'install': 'cargo (nightly)', 'preset': {'build': ['cargo build --release -q'], 'run': 'target/release/runner'}},
    {'id': 'binius', 'name': 'Binius', 'kind': 'library', 'lang': 'Rust', 'by': 'Irreducible', 'proof': 'Binary-tower SNARK', 'homepage': 'https://www.binius.xyz',
     'install': 'cargo', 'preset': {'build': ['cargo build --release -q'], 'run': 'target/release/runner'}},
    {'id': 'expander', 'name': 'Expander', 'kind': 'library', 'lang': 'Rust · Go', 'by': 'Polyhedra', 'proof': 'GKR', 'homepage': 'https://github.com/PolyhedraZK/Expander',
     'install': 'cargo + ExpanderCompilerCollection', 'preset': {'build': ['cargo build --release -q'], 'run': 'target/release/runner'}},
    {'id': 'snarkjs', 'name': 'snarkjs', 'kind': 'library', 'lang': 'JavaScript', 'by': 'iden3', 'proof': 'Groth16 · PLONK · FFLONK', 'homepage': 'https://github.com/iden3/snarkjs',
     'install': 'npm i -g snarkjs (proves circom projects)', 'preset': {'build': ['npm install'], 'run': 'node runner.mjs'}},
    {'id': 'libsnark', 'name': 'libsnark', 'kind': 'library', 'lang': 'C++', 'by': 'SCIPR Lab', 'proof': 'Groth16 · BCTV14 (R1CS)', 'homepage': 'https://github.com/scipr-lab/libsnark',
     'install': 'cmake build', 'preset': {'build': ['cmake -B target && cmake --build target'], 'run': 'target/runner'}},
]


def catalog():
    """CATALOG with the live state of each: driven by an adapter (and
    installed?) or reachable through the runner protocol."""
    out = []
    for c in CATALOG:
        f = FRAMEWORKS.get(c['id'])
        e = {**c, 'driven': bool(f), 'via': c['id'] if f else 'custom'}
        if f:
            e.update(title=f.title, installed=f.installed(), install=f.install,
                     blurb=f.blurb, runs=f.runs, sandbox=f.sandbox,
                     templates=[t['id'] for t in f.templates()])
        else:
            e.update(title=c.get('name') or c['id'], installed=None, runs=True,
                     sandbox=False, templates=[])
        out.append(e)
    return out


def get(fid):
    f = FRAMEWORKS.get(fid or DEFAULT)
    if not f:
        raise ValueError(f'unknown framework {fid!r}; known: {sorted(FRAMEWORKS)}')
    return f


def detect(d, declared=None):
    """The framework of project dir d: config.json `framework` wins, else the
    first marker found. None when d is not a project. A catalog framework
    without an adapter (risc0, sp1, ...) is driven by Custom."""
    if declared in FRAMEWORKS and FRAMEWORKS[declared].detect(d):
        return FRAMEWORKS[declared]
    if declared and declared not in FRAMEWORKS and FRAMEWORKS['custom'].detect(d):
        return FRAMEWORKS['custom']
    for f in FRAMEWORKS.values():
        if f.detect(d):
            return f
    return None


def all_templates():
    return [t for f in FRAMEWORKS.values() for t in f.templates()]
