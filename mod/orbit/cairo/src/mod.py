"""cairo — a Cairo/Starknet ZK contract workbench.

Each contract is a self-contained Scarb project living as a mod of its own
under orbit/cairo/src/mods/<name>/ (addressed `cairo.<name>`). This anchor is thin orchestration (Python stdlib only)
over the real toolchain already installed on the box:

    scarb 2.8.4        build / compile Cairo 2 -> Sierra + CASM
    snforge 0.31.0     starknet-foundry unit/integration tests
    sncast 0.31.0      declare / deploy / invoke on the local sandbox
    starknet-devnet    the sandbox chain every contract's app runs on
                       (src/sandbox.py)

Three verbs the user asked for:
    make      -> new(name)            scaffold a contract submod
    vibecode  -> vibe(name, prompt)   drive the Claude CLI on one submod,
                                       then verify with build + test
    test      -> build(name)/test(name)

and one to use the result: every contract gets an app at /cairo/<name>/ — a
plain HTML page over its ABI (reads, writes, events, act-as account) running
against a local devnet. The default page is src/templates/app.html; a
contract can ship its own at src/mods/<name>/app/index.html.
"""

import glob
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time

# src/ is the implementation dir; each contract is a mod of its own under
# src/mods/<name>/ (the protocol's submod convention: path2name drops `src`
# and `mods`, so orbit/cairo/src/mods/counter is addressed `cairo.counter`).
SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
MODS = os.path.join(SRC, 'mods')
TEMPLATES = os.path.join(SRC, 'templates')
SERVER = os.path.join(SRC, 'api', 'server.py')
DEFAULT_APP = os.path.join(TEMPLATES, 'app.html')
BIN = os.path.expanduser('~/.local/bin')

# dirs that are part of the module itself, never a contract submod
RESERVED = {'src', 'mods', 'templates', 'api', 'app', 'releases', 'dist',
            'node_modules', '__pycache__', '.git'}

def _step_brief(s):
    """One agent step, trimmed for the job table: tool, a short target, ok."""
    p = s.get('params') or {}
    what = (p.get('command') or p.get('file_path') or p.get('path')
            or p.get('pattern') or p.get('summary') or s.get('result') or '')
    return {'tool': s.get('tool'), 'what': str(what).strip()[:160],
            'error': bool(s.get('error'))}


CAIRO_SYSTEM_PROMPT = (
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
    "docs/simple-apps.md in the cairo module."
)


# ---------------------------------------------------------------------------
# naming helpers
# ---------------------------------------------------------------------------

def _pkg(name):
    """Normalise a user name into a snake_case Scarb package name."""
    s = re.sub(r'[^0-9a-zA-Z]+', '_', str(name)).strip('_').lower()
    if not s:
        raise ValueError('empty contract name')
    if s[0].isdigit():
        s = 'c_' + s
    return s


def _short(ty):
    """core::integer::u256 -> u256, Span<core::felt252> -> Span<felt252>."""
    ty = (ty or '').replace('::<', '<')
    return re.sub(r'\b(?:[A-Za-z_][A-Za-z0-9_]*::)+', '', ty)


def _pascal(pkg):
    return ''.join(p.capitalize() for p in pkg.split('_') if p)


# ---------------------------------------------------------------------------
# log shaping
# ---------------------------------------------------------------------------

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


def _clean_log(log):
    # everything between cargo starting and finishing the plugin is rustc's
    _plugin = re.compile(r'^(\s*Compiling snforge_scarb_plugin[^\n]*\n).*?'
                         r'^(\s*Finished `release`[^\n]*\n)', re.S | re.M)
    log = _plugin.sub(r'\1\2', log or '')
    log = _RUST_WARN.sub('', log)
    return re.sub(r'\n{3,}', '\n\n', log).strip()


def _summarize_tests(log):
    """Pull per-test verdicts + gas and the totals line out of snforge output."""
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


# Cairo compiler diagnostics: "error: ...\n --> path:line:col"
_DIAG = re.compile(r'^(error|warning)(?:\[\w+\])?: (.+?)\n\s*--> ([^:\n]+):(\d+):(\d+)', re.M)


def _diagnostics(log, root):
    out = []
    for m in _DIAG.finditer(log or ''):
        path = m.group(3)
        if os.path.isabs(path):
            path = os.path.relpath(path, root)
        if path.startswith('..'):
            continue  # inside a dependency, not the user's code
        out.append({'level': m.group(1), 'message': m.group(2).strip(),
                    'path': path, 'line': int(m.group(4)),
                    'col': int(m.group(5))})
    return out


class Mod:
    description = (
        "A Cairo/Starknet ZK contract workbench: scaffold, vibecode, build and "
        "snforge-test Cairo 2 contracts, each one its own submod. Thin stdlib "
        "orchestration over scarb 2.8.4 + starknet-foundry 0.31.")
    path = ROOT
    config = json.load(open(os.path.join(ROOT, 'config.json')))
    port = config['port']
    base = config['base_path']

    # ---- module protocol ---------------------------------------------------

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {
            'name': 'cairo',
            'description': self.description,
            'path': self.path,
            'port': self.port,
            'toolchain': self.toolchain(),
            'contracts': [c['name'] for c in self._catalog()],
            'fns': self.config['fns'],
            'endpoints': self.config['endpoints'],
        }

    def health(self):
        return {'ok': True, 'module': 'cairo',
                'scarb': self._tool('scarb') is not None,
                'contracts': len(self._catalog())}

    def readme(self):
        p = os.path.join(self.path, 'README.md')
        return open(p).read() if os.path.exists(p) else None

    # ---- toolchain ----------------------------------------------------------

    def _tool(self, name):
        """Absolute path to a toolchain binary, ~/.local/bin first."""
        local = os.path.join(BIN, name)
        if os.path.exists(local):
            return local
        return shutil.which(name)

    def _version(self, name):
        exe = self._tool(name)
        if not exe:
            return None
        try:
            out = subprocess.run([exe, '--version'], capture_output=True,
                                 text=True, timeout=15)
            return out.stdout.strip().splitlines()[0] if out.stdout else None
        except Exception as e:  # noqa: BLE001
            return f'error: {e}'

    def toolchain(self):
        """Which Cairo tools are present and their versions."""
        tools = {}
        for t in ('scarb', 'snforge', 'sncast', 'universal-sierra-compiler'):
            exe = self._tool(t)
            tools[t] = {'path': exe, 'version': self._version(t) if exe else None}
        tools['claude'] = {'path': self._tool('claude'),
                           'creds': os.path.exists(
                               os.path.expanduser('~/.claude/.credentials.json'))}
        return tools

    # ---- submod catalog -----------------------------------------------------

    def _subdir(self, name):
        """Validate + resolve a submod directory, no path escapes."""
        pkg = _pkg(name)
        if pkg in RESERVED:
            raise ValueError(f'{pkg!r} is a reserved directory')
        d = os.path.join(MODS, pkg)
        if os.path.dirname(os.path.abspath(d)) != os.path.abspath(MODS):
            raise ValueError('invalid contract name')
        return pkg, d

    def _is_submod(self, d):
        return os.path.isfile(os.path.join(d, 'Scarb.toml'))

    def _build_state(self, d):
        """unbuilt | stale (a source is newer than the artifacts) | fresh."""
        dev = os.path.join(d, 'target', 'dev')
        arts = glob.glob(os.path.join(dev, '*.contract_class.json'))
        arts = [a for a in arts if '.test.' not in a]
        if not arts:
            return 'unbuilt'
        built_at = min(os.path.getmtime(a) for a in arts)
        srcs = [os.path.join(d, 'Scarb.toml')] + glob.glob(
            os.path.join(d, 'src', '**', '*.cairo'), recursive=True)
        newest = max((os.path.getmtime(s) for s in srcs if os.path.exists(s)),
                     default=0)
        return 'stale' if newest > built_at else 'fresh'

    def _sub_meta(self, d):
        try:
            return json.load(open(os.path.join(d, 'config.json')))
        except Exception:  # noqa: BLE001
            return {}

    def _catalog(self):
        out = []
        if not os.path.isdir(MODS):
            return out
        for entry in sorted(os.listdir(MODS)):
            d = os.path.join(MODS, entry)
            if entry in RESERVED or not os.path.isdir(d):
                continue
            if self._is_submod(d):
                meta = self._sub_meta(d)
                state = self._build_state(d)
                out.append({'name': entry, 'path': d,
                            'built': state != 'unbuilt', 'state': state,
                            'template': meta.get('template'),
                            'description': meta.get('description')})
        return out

    def list(self):
        """List every contract submod with a quick status."""
        return {'count': len(self._catalog()), 'contracts': self._catalog()}

    def contracts(self):
        return self.list()

    def get(self, name):
        """One submod: its Scarb.toml, source tree and build state."""
        pkg, d = self._subdir(name)
        if not self._is_submod(d):
            return {'error': f'no such contract {pkg!r}', 'known':
                    [c['name'] for c in self._catalog()]}
        return {
            'name': pkg,
            'path': d,
            'submod_of': 'cairo',
            'scarb_toml': self._read(os.path.join(d, 'Scarb.toml')),
            'files': self._tree(d),
            'built': self._build_state(d) != 'unbuilt',
            'state': self._build_state(d),
            'description': self._sub_meta(d).get('description'),
        }

    def tree(self, name):
        pkg, d = self._subdir(name)
        return {'name': pkg, 'files': self._tree(d)}

    def _tree(self, root):
        files = []
        for p in glob.glob(os.path.join(root, '**', '*'), recursive=True):
            if any(seg in ('target', '.snfoundry_cache', '.git')
                   for seg in p.split(os.sep)):
                continue
            if os.path.isfile(p):
                files.append(os.path.relpath(p, root))
        return sorted(files)

    # ---- file ops (scoped inside a submod) ---------------------------------

    def _safe_join(self, d, rel):
        full = os.path.abspath(os.path.join(d, rel))
        if full != d and not full.startswith(d + os.sep):
            raise ValueError('path escapes the contract directory')
        return full

    def _read(self, p):
        try:
            return open(p).read()
        except Exception:  # noqa: BLE001
            return None

    def read(self, name, path):
        pkg, d = self._subdir(name)
        full = self._safe_join(d, path)
        if not os.path.isfile(full):
            return {'error': f'no such file {path!r}'}
        return {'name': pkg, 'path': path, 'content': self._read(full)}

    def write(self, name, path, content):
        pkg, d = self._subdir(name)
        full = self._safe_join(d, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as f:
            f.write(content)
        return {'name': pkg, 'path': path, 'bytes': len(content)}

    # ---- make: scaffold a contract submod ----------------------------------

    def templates(self):
        """Starter contracts new() can scaffold from (src/templates/<id>/)."""
        out = []
        for d in sorted(glob.glob(os.path.join(TEMPLATES, '*', 'lib.cairo.tmpl'))):
            tid = os.path.basename(os.path.dirname(d))
            meta = {}
            try:
                meta = json.load(open(os.path.join(os.path.dirname(d), 'meta.json')))
            except Exception:  # noqa: BLE001
                pass
            out.append({'id': tid, 'title': meta.get('title', tid),
                        'blurb': meta.get('blurb', ''),
                        'order': meta.get('order', 99)})
        return {'templates': sorted(out, key=lambda t: (t['order'], t['id']))}

    def new(self, name, desc=None, template='counter', force=False):
        """Scaffold a new Cairo contract submod from a starter template
        (see templates(): counter, token, commit_reveal, merkle_allowlist)."""
        pkg, d = self._subdir(name)
        if os.path.exists(d) and not force:
            if self._is_submod(d):
                return {'error': f'{pkg!r} already exists', 'name': pkg,
                        'path': d}
        template = template or 'counter'
        known = [t['id'] for t in self.templates()['templates']]
        if template not in known:
            return {'error': f'unknown template {template!r}', 'templates': known}
        cls = _pascal(pkg)
        ctx = {'PKG': pkg, 'NAME': cls,
               'DESC': desc or f'{cls} — a Cairo contract.'}

        def render(tmpl):
            # per-template file first, shared file (Scarb.toml) as fallback
            p = os.path.join(TEMPLATES, template, tmpl)
            if not os.path.exists(p):
                p = os.path.join(TEMPLATES, tmpl)
            with open(p) as f:
                s = f.read()
            for k, v in ctx.items():
                s = s.replace('{{' + k + '}}', v)
            return s

        os.makedirs(os.path.join(d, 'src'), exist_ok=True)
        os.makedirs(os.path.join(d, 'tests'), exist_ok=True)
        self._emit(os.path.join(d, 'Scarb.toml'), render('Scarb.toml.tmpl'))
        self._emit(os.path.join(d, 'src', 'lib.cairo'),
                   render('lib.cairo.tmpl'))
        self._emit(os.path.join(d, 'tests', f'test_{pkg}.cairo'),
                   render('test.cairo.tmpl'))
        self._emit(os.path.join(d, '.gitignore'), 'target/\n.snfoundry_cache/\n')
        if os.path.exists(os.path.join(TEMPLATES, template, 'app.json')):
            self._emit(os.path.join(d, 'app.json'), render('app.json'))
        self._emit(os.path.join(d, 'config.json'), json.dumps({
            'name': pkg,
            'title': cls,
            'description': ctx['DESC'],
            'version': '0.1.0',
            'submod_of': 'cairo',
            'kind': 'cairo-contract',
            'template': template,
            'edition': '2024_07',
        }, indent=4) + '\n')
        self._as_mod(pkg, d)
        return {'name': pkg, 'contract': cls, 'path': d, 'template': template,
                'mod': f'cairo.{pkg}',
                'files': self._tree(d),
                'next': [f"m cairo.{pkg}/vibe prompt='...'",
                         f'm cairo.{pkg}/test']}

    def _emit(self, path, content):
        with open(path, 'w') as f:
            f.write(content)

    # every contract dir is also a protocol mod: its own mod.py anchor (the
    # parent's fns bound to this contract), README and config anchor/fns
    SUB_FNS = ['forward', 'info', 'build', 'test', 'vibe', 'abi', 'tree',
               'read', 'write', 'clean', 'app', 'deploy', 'call', 'events']

    def _as_mod(self, pkg, d):
        meta = self._sub_meta(d)
        desc = meta.get('description') or f'{_pascal(pkg)} — a Cairo contract.'
        with open(os.path.join(TEMPLATES, 'mod.py.tmpl')) as f:
            src = f.read()
        src = src.replace('{{PKG}}', pkg).replace('{{DESC_PY}}', repr(desc))
        self._emit(os.path.join(d, 'mod.py'), src)
        readme = os.path.join(d, 'README.md')
        if not os.path.exists(readme):
            self._emit(readme, (
                f'# {pkg}\n\n{desc}\n\n'
                f'A Cairo contract mod (submod of `cairo`), addressed '
                f'`cairo.{pkg}`. A self-contained Scarb project: '
                f'`src/lib.cairo` is the contract, `tests/` the snforge '
                f'tests.\n\n```sh\nm cairo.{pkg}/build\n'
                f'm cairo.{pkg}/test\nm cairo.{pkg}/vibe prompt="..."\n```\n'))
        meta.update({'name': pkg, 'submod_of': 'cairo', 'anchor': 'mod.py',
                     'fns': self.SUB_FNS})
        self._emit(os.path.join(d, 'config.json'),
                   json.dumps(meta, indent=4) + '\n')
        return d

    # ---- market: every contract as a listing, and fork one into a new mod ---

    def market(self):
        """The bazaar: every contract with what a browser wants on a card —
        build state, ABI summary, sandbox deployment, size, last touched —
        plus the starter templates. One call, no devnet boot."""
        sb = self._sandbox()
        deps = {}
        try:
            deps = sb.deployments()
        except Exception:  # noqa: BLE001
            pass
        out = []
        for c in self._catalog():
            d, meta = c['path'], self._sub_meta(c['path'])
            abi = self.abi(c['name']).get('contracts', []) if c['built'] else []
            fns = [f for a in abi for f in a['functions']]
            srcs = glob.glob(os.path.join(d, 'src', '**', '*.cairo'), recursive=True)
            tests = glob.glob(os.path.join(d, 'tests', '**', '*.cairo'), recursive=True)
            lines = sum(len((self._read(p) or '').splitlines()) for p in srcs)
            touched = max((os.path.getmtime(p) for p in srcs + tests), default=0)
            dep = deps.get(c['name'])
            out.append({
                'name': c['name'], 'mod': f"cairo.{c['name']}",
                'title': meta.get('title') or _pascal(c['name']),
                'description': c['description'], 'template': c['template'],
                'forked_from': meta.get('forked_from'),
                'state': c['state'], 'lines': lines, 'tests': len(tests),
                'touched': touched,
                'reads': [f['name'] for f in fns if f['mutability'] == 'view'],
                'writes': [f['name'] for f in fns if f['mutability'] != 'view'],
                'events': [e['name'] for a in abi for e in a['events']],
                'sierra_felts': sum(a['sierra_felts'] for a in abi),
                'custom_app': os.path.isfile(os.path.join(d, 'app', 'index.html')),
                'deployed': ({'address': dep.get('address'), 'by': dep.get('by'),
                              'at': dep.get('at')} if dep else None),
                'url': f"{self.base}/{c['name']}/",
            })
        out.sort(key=lambda x: -x['touched'])
        return {'count': len(out), 'contracts': out,
                'templates': self.templates()['templates']}

    def fork(self, src, name, desc=None):
        """Copy a contract into a new mod under a new name: package, contract
        module (PascalCase), dispatchers and `use <pkg>::` paths renamed so the
        copy builds and tests on its own."""
        spkg, sd = self._contract(src)
        pkg, d = self._subdir(name)
        if os.path.exists(d):
            return {'error': f'{pkg!r} already exists'}
        shutil.copytree(sd, d, ignore=shutil.ignore_patterns(
            'target', '.snfoundry_cache', '__pycache__', 'Scarb.lock',
            'mod.py', 'README.md'))
        old, new = _pascal(spkg), _pascal(pkg)
        # `Old` alone or as a prefix of a type (IOld, OldDispatcher, ...)
        cls_re = re.compile(r'(?<![A-Za-z0-9_])(I?)' + re.escape(old) + r'(?![a-z0-9_])')
        pkg_re = re.compile(r'\b' + re.escape(spkg) + r'::')
        for p in glob.glob(os.path.join(d, '**', '*.cairo'), recursive=True):
            s = self._read(p) or ''
            s = cls_re.sub(lambda m: m.group(1) + new, pkg_re.sub(pkg + '::', s))
            self._emit(p, s)
            base = os.path.basename(p)
            if spkg in base:
                os.rename(p, os.path.join(os.path.dirname(p), base.replace(spkg, pkg)))
        toml = os.path.join(d, 'Scarb.toml')
        self._emit(toml, re.sub(r'(?m)^name = "[^"]*"', f'name = "{pkg}"',
                                self._read(toml) or '', count=1))
        meta = self._sub_meta(d)
        meta.update({'name': pkg, 'title': new, 'forked_from': spkg,
                     'description': desc or f'{new} — forked from {spkg}.'})
        self._emit(os.path.join(d, 'config.json'), json.dumps(meta, indent=4) + '\n')
        self._as_mod(pkg, d)
        return {'name': pkg, 'contract': new, 'forked_from': spkg,
                'mod': f'cairo.{pkg}', 'files': self._tree(d)}

    def adopt(self):
        """Give every contract under src/mods/ the protocol files (mod.py,
        README, config anchor/fns) and pull any legacy contract still at the
        module root (orbit/cairo/<name>/) into src/mods/."""
        moved = []
        for entry in sorted(os.listdir(ROOT)):
            d = os.path.join(ROOT, entry)
            if entry not in RESERVED and os.path.isdir(d) and self._is_submod(d):
                os.makedirs(MODS, exist_ok=True)
                shutil.move(d, os.path.join(MODS, entry))
                moved.append(entry)
        for c in self._catalog():
            self._as_mod(c['name'], c['path'])
        return {'moved': moved, 'mods': [f"cairo.{c['name']}"
                                         for c in self._catalog()]}

    # ---- vibecode: hand one submod to the claudecode agent ------------------
    # For now vibe speaks the agent protocol (GET /agents + POST /run/stream,
    # SSE step/done/error) to orbit/claudecode on loopback, so the CLI spawn
    # spec, creds and cost metering live in ONE place. If that server is down
    # we fall back to spawning the CLI ourselves, same prompt, same scope.

    AGENT_URL = os.environ.get('CAIRO_AGENT_URL', 'http://127.0.0.1:51260')

    def _agent_goal(self, d):
        # the agent server's PATH doesn't carry ~/.local/bin, so say where the
        # toolchain is, and pin the run to this one contract dir
        return (CAIRO_SYSTEM_PROMPT +
                f"- Work ONLY inside {d}. Use absolute paths.\n"
                f"- The toolchain is in {BIN}: run `export PATH={BIN}:$PATH` "
                f"first (scarb, snforge, sncast).\n")

    def _vibe_agent(self, d, prompt, model, timeout, on_step=None):
        """POST /run/stream to the claudecode agent. Returns (ok, text, meta,
        steps) or None when the agent server can't be reached."""
        import urllib.request
        import urllib.error
        body = json.dumps({'query': str(prompt), 'goal': self._agent_goal(d),
                           'path': d, 'model': model or None,
                           'timeout': int(timeout)}).encode()
        req = urllib.request.Request(self.AGENT_URL.rstrip('/') + '/run/stream',
                                     data=body, method='POST',
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
                    steps.append(ev.get('step') or {})
                    if on_step:
                        try:
                            on_step(ev.get('step') or {})
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

    def _vibe_cli(self, d, prompt, model, timeout):
        claude = self._tool('claude')
        if not claude:
            return False, 'claude CLI not found and agent server unreachable', {}, []
        cmd = [claude, '-p', str(prompt),
               '--append-system-prompt', CAIRO_SYSTEM_PROMPT,
               '--permission-mode', 'acceptEdits',
               '--add-dir', d,
               '--output-format', 'json']
        if model:
            cmd += ['--model', str(model)]
        env = dict(os.environ)
        env['PATH'] = BIN + os.pathsep + env.get('PATH', '')
        try:
            out = subprocess.run(cmd, cwd=d, capture_output=True, text=True,
                                 timeout=int(timeout), env=env)
        except subprocess.TimeoutExpired:
            return False, f'vibe timed out after {timeout}s', {}, []
        text, meta = out.stdout, {'agent': 'claude-cli'}
        try:
            parsed = json.loads(out.stdout)
            text = parsed.get('result', out.stdout)
            meta.update({k: parsed.get(k) for k in
                         ('num_turns', 'total_cost_usd', 'session_id', 'is_error')
                         if k in parsed})
        except Exception:  # noqa: BLE001
            pass
        return out.returncode == 0, (text or out.stderr or ''), meta, []

    def vibe(self, name, prompt, model=None, verify=True, timeout=1800,
             on_step=None):
        """Edit a contract submod with the claudecode agent (agent protocol,
        :51260; CLI fallback), then (by default) build + test it."""
        pkg, d = self._subdir(name)
        if not self._is_submod(d):
            return {'error': f'no such contract {pkg!r}; run new first'}
        r = self._vibe_agent(d, prompt, model, timeout, on_step)
        if r is None:
            r = self._vibe_cli(d, prompt, model, timeout)
        ok, text, meta, steps = r
        res = {'name': pkg, 'ok': ok, 'meta': meta,
               'result': str(text or '').strip()[-6000:],
               'steps': [_step_brief(s) for s in steps][-60:]}
        if verify and ok:
            res['build'] = self.build(pkg)
            res['test'] = self.test(pkg)
        res['files'] = self._tree(d)
        return res

    # ---- test / build -------------------------------------------------------

    def _run(self, tool, args, cwd, timeout=900):
        exe = self._tool(tool)
        if not exe:
            return {'ok': False, 'error': f'{tool} not installed'}
        env = dict(os.environ)
        env['PATH'] = BIN + os.pathsep + env.get('PATH', '')
        try:
            out = subprocess.run([exe, *args], cwd=cwd, capture_output=True,
                                 text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return {'ok': False, 'error': f'{tool} timed out after {timeout}s'}
        return {'ok': out.returncode == 0, 'code': out.returncode,
                'log': _clean_log(out.stdout + out.stderr)[-12000:]}

    def build(self, name, timeout=900):
        """scarb build one contract submod. Returns the log, any compiler
        diagnostics pinned to file:line, and the ABI on success."""
        pkg, d = self._subdir(name)
        if not self._is_submod(d):
            return {'error': f'no such contract {pkg!r}'}
        t0 = time.time()
        r = self._run('scarb', ['build'], d, timeout)
        r['name'] = pkg
        r['seconds'] = round(time.time() - t0, 1)
        r['diagnostics'] = _diagnostics(r.get('log'), d)
        if r.get('ok'):
            r['artifacts'] = self.abi(pkg).get('contracts', [])
        return r

    def abi(self, name):
        """The built contract classes: entrypoints, events, constructor and
        Sierra/CASM size, read from target/dev. Build first."""
        pkg, d = self._subdir(name)
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
        return {'name': pkg, 'state': self._build_state(d), 'contracts': out}

    @staticmethod
    def _abi_fn(f, iface):
        return {'name': f['name'],
                'interface': iface.split('::')[-1] if iface else None,
                'mutability': f.get('state_mutability', 'external'),
                'inputs': [{'name': i['name'], 'type': _short(i['type'])}
                           for i in f.get('inputs', [])],
                'outputs': [_short(o['type']) for o in f.get('outputs', [])]}

    # ---- the app: a page per contract over a local devnet -------------------

    def _sandbox(self):
        # by path, same reason as mod.py itself: no bare-name imports here
        if not hasattr(self, '_sb'):
            spec = importlib.util.spec_from_file_location(
                'cairo_sandbox', os.path.join(SRC, 'sandbox.py'))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            self._sb = m
        return self._sb

    def _contract(self, name):
        pkg, d = self._subdir(name)
        if not self._is_submod(d):
            raise ValueError(f'no such contract {pkg!r}')
        return pkg, d

    def app(self, name):
        """Everything a contract's app page needs in one call: ABI, sandbox
        deployment, accounts to act as, and whether the page is custom."""
        pkg, d = self._contract(name)
        sb = self._sandbox()
        custom = os.path.isfile(os.path.join(d, 'app', 'index.html'))
        # app.json = the plain-language layer: labels, hidden inputs/outputs,
        # visuals. Optional; a broken one is reported, never fatal.
        ui, ui_error = {}, None
        try:
            with open(os.path.join(d, 'app.json')) as f:
                ui = json.load(f)
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            ui_error = f'app.json: {e}'
        # opening the page boots the chain (~1s, reloads its dump), so the
        # deployment and accounts below are real, not "down"
        if self._build_state(d) != 'unbuilt':
            sb.start()
        return {'name': pkg, 'title': _pascal(pkg),
                'description': self._sub_meta(d).get('description'),
                'url': f'{self.base}/{pkg}/', 'custom': custom,
                'page': f'src/mods/{pkg}/app/index.html' if custom
                else 'src/templates/app.html',
                'state': self._build_state(d),
                'ui': ui, 'ui_error': ui_error,
                'abi': self.abi(pkg).get('contracts', []),
                'devnet': {'up': sb.alive(), 'rpc': sb.RPC + '/rpc'},
                'deployment': sb.deployment(pkg),
                'accounts': [{'name': a['name'], 'address': a['address']}
                             for a in sb.accounts()]}

    def deploy(self, name, args=None, contract=None, account='alice'):
        """Declare + deploy a built contract on the local devnet sandbox
        (started on demand). args = constructor args by name."""
        pkg, d = self._contract(name)
        if self._build_state(d) == 'unbuilt':
            return {'ok': False, 'error': 'build it first'}
        try:
            return self._sandbox().deploy(pkg, d, args, contract, account)
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'error': str(e)}

    def call(self, name, fn, args=None, account='alice'):
        """Call one entrypoint of the deployed contract. Views read; anything
        else is a transaction signed by the sandbox `account`
        (alice/bob/carol/dave). Args are typed values by name."""
        pkg, d = self._contract(name)
        try:
            return self._sandbox().call(pkg, d, fn, args, account)
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'error': str(e)}

    def events(self, name, limit=50):
        """Decoded events the deployed contract has emitted, newest first."""
        pkg, d = self._contract(name)
        try:
            return self._sandbox().events(pkg, d, limit)
        except Exception as e:  # noqa: BLE001
            return {'events': [], 'error': str(e)}

    def sandbox(self, action='status'):
        """The devnet: status | start | stop | reset (drops every deploy)."""
        sb = self._sandbox()
        if action == 'start':
            return sb.start()
        if action == 'stop':
            return sb.stop()
        if action == 'reset':
            return sb.reset()
        return {'up': sb.alive(), 'port': sb.DEVNET_PORT,
                'accounts': [{'name': a['name'], 'address': a['address']}
                             for a in sb.accounts()],
                'deployments': sb.deployments()}

    def test(self, name=None, timeout=1200):
        """snforge test one submod; with no name, run the module self-test."""
        if name is None:
            return self.status(deep=False)
        pkg, d = self._subdir(name)
        if not self._is_submod(d):
            return {'error': f'no such contract {pkg!r}'}
        t0 = time.time()
        # snforge compiles only the test crate, never the deployable class, so
        # build first when stale: the ABI and "built" state then track what
        # was tested, and a compile error surfaces with clean diagnostics
        built = None
        if self._build_state(d) != 'fresh':
            built = self.build(pkg, timeout)
            if not built.get('ok'):
                built.update(name=pkg, tests=[], totals=None, stage='build')
                return built
        r = self._run('snforge', ['test'], d, timeout)
        r['name'] = pkg
        r['seconds'] = round(time.time() - t0, 1)
        r.update(_summarize_tests(r.get('log')))
        r['diagnostics'] = _diagnostics(r.get('log'), d)
        if built:
            r['artifacts'] = built.get('artifacts', [])
        return r

    def clean(self, name):
        """Remove a submod's scarb target/ and snforge cache."""
        pkg, d = self._subdir(name)
        removed = []
        for sub in ('target', '.snfoundry_cache'):
            p = os.path.join(d, sub)
            if os.path.isdir(p):
                shutil.rmtree(p)
                removed.append(sub)
        return {'name': pkg, 'removed': removed}

    def remove(self, name):
        """Delete a contract submod entirely."""
        pkg, d = self._subdir(name)
        if not self._is_submod(d):
            return {'error': f'no such contract {pkg!r}'}
        shutil.rmtree(d)
        return {'removed': pkg, 'path': d}

    # ---- status / self-test -------------------------------------------------

    def status(self, deep=False):
        """Toolchain + catalog. deep=True builds and tests the examples."""
        s = {'module': 'cairo', 'toolchain': self.toolchain(),
             'contracts': self._catalog()}
        if deep:
            s['checks'] = {c['name']: {'build': self.build(c['name']),
                                       'test': self.test(c['name'])}
                           for c in self._catalog()}
        return s

    # ---- HTTP server (src/api/server.py) ------------------------------------

    def serve(self, port=None, background=True):
        """Run the API + console (src/api/server.py) on the module port:
        /cairo = app, /cairo/api/<fn> = protocol API, /cairo/_api/<fn> = the
        app's self-calls. background=False runs it in the foreground."""
        port = int(port or self.port)
        cmd = [sys.executable, SERVER, str(port)]
        if not background:
            os.execv(sys.executable, cmd)
        self.kill(port)
        log = open(os.path.join(ROOT, 'server.log'), 'a')
        proc = subprocess.Popen(cmd, stdout=log, stderr=log, cwd=ROOT,
                                start_new_session=True)
        return {'pid': proc.pid, 'port': port,
                'app': f'http://localhost:{port}{self.base}',
                'api': f'http://localhost:{port}{self.base}/api'}

    def kill(self, port=None):
        port = int(port or self.port)
        out = subprocess.run(['fuser', '-k', f'{port}/tcp'],
                             capture_output=True, text=True)
        return {'port': port, 'killed': out.returncode == 0}


if __name__ == '__main__':
    m = Mod()
    if len(sys.argv) > 1 and sys.argv[1] == 'serve':
        m.serve(port=sys.argv[2] if len(sys.argv) > 2 else None,
                background=False)
    else:
        print(json.dumps(m.status(), indent=2, default=str))
