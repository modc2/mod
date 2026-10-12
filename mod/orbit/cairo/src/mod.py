"""cairo — a Cairo/Starknet ZK contract workbench.

Each contract is a self-contained Scarb project living as a submod under
orbit/cairo/<name>/. This anchor is thin orchestration (Python stdlib only)
over the real toolchain already installed on the box:

    scarb 2.8.4        build / compile Cairo 2 -> Sierra + CASM
    snforge 0.31.0     starknet-foundry unit/integration tests
    sncast 0.31.0      declare / deploy (left to the user, not driven here)

Three verbs the user asked for:
    make      -> new(name)            scaffold a contract submod
    vibecode  -> vibe(name, prompt)   drive the Claude CLI on one submod,
                                       then verify with build + test
    test      -> build(name)/test(name)
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys

# src/ is the implementation dir; contract submods live one level up at the
# module root (orbit/cairo/<name>/), so every path is anchored on ROOT.
SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
TEMPLATES = os.path.join(SRC, 'templates')
SERVER = os.path.join(SRC, 'api', 'server.py')
BIN = os.path.expanduser('~/.local/bin')

# dirs that are part of the module itself, never a contract submod
RESERVED = {'src', 'templates', 'api', 'app', 'releases', 'dist',
            'node_modules', '__pycache__', '.git'}

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
    "- Keep the public interface coherent and comment WHY, not WHAT."
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


def _pascal(pkg):
    return ''.join(p.capitalize() for p in pkg.split('_') if p)


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
        d = os.path.join(self.path, pkg)
        if os.path.dirname(os.path.abspath(d)) != os.path.abspath(self.path):
            raise ValueError('invalid contract name')
        return pkg, d

    def _is_submod(self, d):
        return os.path.isfile(os.path.join(d, 'Scarb.toml'))

    def _catalog(self):
        out = []
        for entry in sorted(os.listdir(self.path)):
            d = os.path.join(self.path, entry)
            if entry in RESERVED or not os.path.isdir(d):
                continue
            if self._is_submod(d):
                out.append({'name': entry, 'path': d,
                            'built': os.path.isdir(os.path.join(d, 'target'))})
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
            'built': os.path.isdir(os.path.join(d, 'target')),
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

    def new(self, name, desc=None, force=False):
        """Scaffold a new Cairo contract submod from templates/."""
        pkg, d = self._subdir(name)
        if os.path.exists(d) and not force:
            if self._is_submod(d):
                return {'error': f'{pkg!r} already exists', 'name': pkg,
                        'path': d}
        cls = _pascal(pkg)
        ctx = {'PKG': pkg, 'NAME': cls,
               'DESC': desc or f'{cls} — a Cairo contract.'}

        def render(tmpl):
            with open(os.path.join(TEMPLATES, tmpl)) as f:
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
        self._emit(os.path.join(d, 'config.json'), json.dumps({
            'name': pkg,
            'title': cls,
            'description': ctx['DESC'],
            'version': '0.1.0',
            'submod_of': 'cairo',
            'kind': 'cairo-contract',
            'edition': '2024_07',
        }, indent=4) + '\n')
        return {'name': pkg, 'contract': cls, 'path': d,
                'files': self._tree(d),
                'next': [f"m cairo/vibe name={pkg} prompt='...'",
                         f'm cairo/test name={pkg}']}

    def _emit(self, path, content):
        with open(path, 'w') as f:
            f.write(content)

    # ---- vibecode: drive the Claude CLI on one submod ----------------------

    def vibe(self, name, prompt, model=None, verify=True, timeout=1800):
        """Edit a contract submod with the headless Claude CLI, then (by
        default) build + test it. The agent is scoped to the submod dir."""
        pkg, d = self._subdir(name)
        if not self._is_submod(d):
            return {'error': f'no such contract {pkg!r}; run new first'}
        claude = self._tool('claude')
        if not claude:
            return {'error': 'claude CLI not found on PATH'}

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
            return {'error': f'vibe timed out after {timeout}s', 'name': pkg}

        result_text, meta = out.stdout, {}
        try:
            parsed = json.loads(out.stdout)
            result_text = parsed.get('result', out.stdout)
            meta = {k: parsed.get(k) for k in
                    ('num_turns', 'total_cost_usd', 'session_id', 'is_error')
                    if k in parsed}
        except Exception:  # noqa: BLE001
            pass

        res = {'name': pkg, 'ok': out.returncode == 0, 'meta': meta,
               'result': (result_text or out.stderr or '').strip()[-6000:]}
        if verify and out.returncode == 0:
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
                'log': (out.stdout + out.stderr).strip()[-8000:]}

    def build(self, name, timeout=900):
        """scarb build one contract submod."""
        pkg, d = self._subdir(name)
        if not self._is_submod(d):
            return {'error': f'no such contract {pkg!r}'}
        r = self._run('scarb', ['build'], d, timeout)
        r['name'] = pkg
        return r

    def test(self, name=None, timeout=1200):
        """snforge test one submod; with no name, run the module self-test."""
        if name is None:
            return self.status(deep=False)
        pkg, d = self._subdir(name)
        if not self._is_submod(d):
            return {'error': f'no such contract {pkg!r}'}
        r = self._run('snforge', ['test'], d, timeout)
        r['name'] = pkg
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
