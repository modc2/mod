"""zkworld — a multi-framework zero-knowledge workbench.

Forked from the cairo workbench and widened: a project can be written in any
ZK framework zkworld has an adapter for (src/frameworks.py):

    cairo    Cairo 2 / Starknet  scarb + snforge, live devnet app (src/sandbox.py)
    circom   Circom 2 circuits   circom -> R1CS + wasm witness
    noir     Noir programs       nargo compile / test / execute
    zokrates ZoKrates DSL        zokrates compile / compute-witness
    leo      Leo (Aleo)          leo build / test / run --offline
    gnark    Go circuits         go build of the project's runner, go test
    halo2    Rust circuits       cargo build of the project's runner, cargo test
    custom   anything else       config.json `zk` block + the runner protocol
                                 (risc0, sp1, arkworks, ... see FW.CATALOG)

All of them share one statement format (tests/*.json), so statements(),
compare() and port_to() connect projects across frameworks.

Each project is a mod of its own under orbit/zkworld/src/mods/<name>/
(addressed `zkworld.<name>`), with `framework` in its config.json. This anchor
is thin orchestration (Python stdlib only): catalog, files, fork, vibe, jobs.
Everything toolchain-specific (build, test, ABI, starters, agent rules) lives
in the framework adapter, so adding a framework never touches this file.

Verbs:
    make      -> new(name, template=, framework=)  scaffold a project
    vibecode  -> vibe(name, prompt)   drive the agent on one project, then
                                       verify with build + test
    test      -> build(name)/test(name)
    use       -> app(name) (+ deploy/call/events on cairo's devnet,
                 witness(name, input) for circuits)
"""

import glob
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time

# src/ is the implementation dir; each project is a mod of its own under
# src/mods/<name>/ (the protocol's submod convention: path2name drops `src`
# and `mods`, so orbit/zkworld/src/mods/counter is addressed `zkworld.counter`).
SRC = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SRC)
MODS = os.path.join(SRC, 'mods')
TEMPLATES = os.path.join(SRC, 'templates')
SERVER = os.path.join(SRC, 'api', 'server.py')
BIN = os.path.expanduser('~/.local/bin')


def _load(name, file):
    # by path under a unique name: bare imports here would hit the protocol's
    # own packages (see mod.py.tmpl)
    spec = importlib.util.spec_from_file_location(name, os.path.join(SRC, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


FW = _load('zkworld_frameworks', 'frameworks.py')
# kept for callers that predate the framework layer (helper/builder prompts)
CAIRO_SYSTEM_PROMPT = FW.FRAMEWORKS['cairo'].prompt

# dirs that are part of the module itself, never a project submod
RESERVED = {'src', 'mods', 'templates', 'api', 'app', 'releases', 'dist',
            'mcp', 'agents', 'run', 'learn',  # server routes (api/server.py)
            'node_modules', '__pycache__', '.git'} | set(FW.FRAMEWORKS)
# every framework is a running submod too: src/mods/<id>/ (zkworld.<id>), its
# own server on FW_PORTS[id], proxied by the parent at /zkworld/<id>/
FW_PORT_BASE = 51353
FW_PORTS = {fid: FW_PORT_BASE + i for i, fid in enumerate(FW.FRAMEWORKS)}
FW_SERVER = os.path.join(SRC, 'api', 'fwserver.py')
FW_WRITE_FNS = ['new', 'build', 'test', 'vibe', 'clean']


def _step_brief(s):
    """One agent step, trimmed for the job table: tool, a short target, ok."""
    p = s.get('params') or {}
    what = (p.get('command') or p.get('file_path') or p.get('path')
            or p.get('pattern') or p.get('summary') or s.get('result') or '')
    return {'tool': s.get('tool'), 'what': str(what).strip()[:160],
            'error': bool(s.get('error'))}


def _pkg(name):
    """Normalise a user name into a snake_case package name."""
    s = re.sub(r'[^0-9a-zA-Z]+', '_', str(name)).strip('_').lower()
    if not s:
        raise ValueError('empty project name')
    if s[0].isdigit():
        s = 'c_' + s
    return s


_pascal = FW.pascal

class Mod:
    description = (
        "A multi-framework zero-knowledge workbench: scaffold, vibecode, build "
        "and test ZK projects in Cairo (Starknet), Circom or Noir, each one its "
        "own submod. Thin stdlib orchestration over each framework's real "
        "toolchain (src/frameworks.py).")
    path = ROOT
    config = json.load(open(os.path.join(ROOT, 'config.json')))
    port = config['port']
    base = config['base_path']

    # ---- module protocol ---------------------------------------------------

    def forward(self, **kwargs):
        return self.info()

    def info(self):
        return {
            'name': 'zkworld',
            'description': self.description,
            'path': self.path,
            'port': self.port,
            'frameworks': [f.describe() for f in FW.FRAMEWORKS.values()],
            'toolchain': self.toolchain(),
            'contracts': [c['name'] for c in self._catalog()],
            'fns': self.config['fns'],
            'endpoints': self.config['endpoints'],
        }

    def tutorials(self, id=None):
        """The LEARN track (src/tutorials.py): stablecoins + Cairo for
        beginners. No id -> the index + glossary; id -> one full lesson."""
        t = _load('zkworld_tutorials', 'tutorials.py')  # re-read each call
        if id:
            x = t.lesson(id)
            if not x:
                return {'error': f'no lesson {id!r}', 'lessons': [l['id'] for l in t.LESSONS]}
            ids = [l['id'] for l in t.LESSONS]
            i = ids.index(id)
            return {**x, 'prev': ids[i - 1] if i else None,
                    'next': ids[i + 1] if i + 1 < len(ids) else None}
        return {'lessons': t.index(), 'glossary': t.GLOSSARY}

    def health(self):
        return {'ok': True, 'module': 'zkworld',
                'frameworks': {k: f.installed() for k, f in FW.FRAMEWORKS.items()},
                'contracts': len(self._catalog())}

    def readme(self):
        p = os.path.join(self.path, 'README.md')
        return open(p).read() if os.path.exists(p) else None

    # ---- frameworks + toolchain --------------------------------------------

    def frameworks(self, id=None):
        """Every ZK framework zkworld knows. `frameworks` = the ones with an
        adapter (tools present, starters, live chain app or not); `catalog` =
        all of them, circuit DSLs, zkVMs and libraries, each either driven by
        its adapter or reachable through the runner protocol (`custom`).
        id -> one, with tool versions."""
        if id:
            if id in FW.FRAMEWORKS:
                return FW.get(id).describe(deep=True)
            hit = [c for c in FW.catalog() if c['id'] == id]
            if not hit:
                raise ValueError(f'unknown framework {id!r}')
            return {**hit[0], 'runner_protocol': FW.Runner.__doc__ or '',
                    'protocol': self._protocol_doc()}
        counts = {}
        for c in self._catalog():
            counts[c['framework']] = counts.get(c['framework'], 0) + 1
        cat = [{**c, 'projects': counts.get(c['id'], 0)} for c in FW.catalog()]
        return {'default': FW.DEFAULT,
                'frameworks': [{**f.describe(), 'projects': counts.get(k, 0)}
                               for k, f in FW.FRAMEWORKS.items()],
                'catalog': cat,
                'kinds': {k: sum(c['kind'] == k for c in cat) for k in ('dsl', 'zkvm', 'library')},
                'protocol': self._protocol_doc()}

    @staticmethod
    def _protocol_doc():
        return {'cases': 'tests/*.json: [{name, input: {k: v}, expect?: {k: v}, fail?: true}] — '
                         'one statement file runs unchanged in every framework; an expected '
                         'name the program takes as an input is fed in',
                'runner': '<runner> --abi -> {circuit, inputs:[{name, public}], outputs:[{name}]}; '
                          '<runner> input.json -> exit 0 + {"outputs":{}} | exit 1 + reason',
                'custom': 'config.json "zk": {build: [cmd..], test?: cmd, run: cmd, env?: {}}'}

    def _tool(self, name):
        return FW.tool(name)

    def toolchain(self):
        """Every framework's tools (path + version), plus the agent CLI. The
        flat tool keys keep the console's status dots working."""
        tools = {}
        for f in FW.FRAMEWORKS.values():
            for t, v in f.toolchain().items():
                tools[t] = {**v, 'framework': f.id}
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
            raise ValueError('invalid project name')
        return pkg, d

    def _fw(self, d):
        """The framework adapter of project dir d (None = not a project;
        a framework submod is a service, not a project)."""
        meta = self._sub_meta(d)
        if meta.get('kind') == 'framework':
            return None
        return FW.detect(d, meta.get('framework'))

    def _is_submod(self, d):
        return os.path.isdir(d) and self._fw(d) is not None

    def _build_state(self, d):
        """unbuilt | stale (a source is newer than the artifacts) | fresh."""
        f = self._fw(d)
        return f.build_state(d) if f else 'unbuilt'

    def default_app(self, d):
        """The page served at /zkworld/<name>/ when the project has no app/."""
        f = self._fw(d) or FW.get(None)
        p = f.default_app()
        return p if os.path.exists(p) else FW.get(None).default_app()

    def _fid(self, d, f):
        """The framework a project says it is: Custom drives catalog
        frameworks (risc0, sp1, ...) but the project keeps its own name."""
        return (self._sub_meta(d).get('framework') or f.id) if f and f.id == 'custom' else f.id

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
            f = self._fw(d)
            if f:
                meta = self._sub_meta(d)
                state = f.build_state(d)
                out.append({'name': entry, 'path': d, 'framework': self._fid(d, f),
                            'built': state != 'unbuilt', 'state': state,
                            'template': meta.get('template'),
                            'statement': meta.get('statement'),
                            'description': meta.get('description')})
        return out

    def list(self):
        """List every contract submod with a quick status."""
        return {'count': len(self._catalog()), 'contracts': self._catalog()}

    def contracts(self):
        return self.list()

    def get(self, name):
        """One project: its manifest (Scarb.toml / Circom.toml / Nargo.toml),
        source tree, framework and build state."""
        pkg, d = self._subdir(name)
        f = self._fw(d)
        if not f:
            return {'error': f'no such project {pkg!r}', 'known':
                    [c['name'] for c in self._catalog()]}
        manifest = self._read(os.path.join(d, f.marker))
        return {
            'name': pkg,
            'path': d,
            'submod_of': 'zkworld',
            'framework': self._fid(d, f),
            'manifest': f.marker,
            # kept under its old key so cairo-era clients keep reading it
            'scarb_toml': manifest,
            'files': self._tree(d),
            'built': f.build_state(d) != 'unbuilt',
            'state': f.build_state(d),
            'description': self._sub_meta(d).get('description'),
        }

    def tree(self, name):
        pkg, d = self._subdir(name)
        return {'name': pkg, 'files': self._tree(d)}

    def _tree(self, root):
        junk = {'.git', '__pycache__'}
        for f in FW.FRAMEWORKS.values():
            junk.update(f.clean_dirs)
        files = []
        for p in glob.glob(os.path.join(root, '**', '*'), recursive=True):
            if any(seg in junk for seg in os.path.relpath(p, root).split(os.sep)):
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

    def templates(self, framework=None):
        """Starter projects new() can scaffold, every framework
        (src/templates/<framework>/<id>/); framework= narrows it."""
        if framework:
            return {'templates': FW.get(framework).templates()}
        return {'templates': FW.all_templates()}

    def new(self, name, desc=None, template=None, framework=None, force=False):
        """Scaffold a new project from a starter. framework= picks the
        toolchain (cairo | circom | noir; default cairo, or whichever
        framework owns `template`); template= the starter within it."""
        pkg, d = self._subdir(name)
        if os.path.exists(d) and not force and self._is_submod(d):
            return {'error': f'{pkg!r} already exists', 'name': pkg, 'path': d}
        if template and not framework:
            owner = [t['framework'] for t in FW.all_templates() if t['id'] == template]
            framework = owner[0] if owner else None
        if framework and framework not in FW.FRAMEWORKS:
            return self._new_custom(pkg, d, framework, desc)
        try:
            f = FW.get(framework)
        except ValueError as e:
            return {'error': str(e), 'frameworks': sorted(FW.FRAMEWORKS)}
        template = template or f.default_template
        known = [t['id'] for t in f.templates()]
        if template not in known:
            return {'error': f'unknown {f.id} template {template!r}', 'templates': known}
        cls = _pascal(pkg)
        ctx = {'PKG': pkg, 'NAME': cls,
               'DESC': desc or f'{cls} — a {f.lang} project.'}
        f.scaffold(pkg, d, template, ctx)
        self._emit(os.path.join(d, 'config.json'), json.dumps({
            'name': pkg,
            'title': cls,
            'description': ctx['DESC'],
            'version': '0.1.0',
            'submod_of': 'zkworld',
            'framework': f.id,
            'kind': f'{f.id}-project' if f.id != 'cairo' else 'cairo-contract',
            'template': template,
            **({'statement': st} if (st := self._starter_statement(f.id, template)) else {}),
        }, indent=4) + '\n')
        self._as_mod(pkg, d)
        return {'name': pkg, 'contract': cls, 'path': d, 'template': template,
                'framework': f.id, 'installed': f.installed(),
                'mod': f'zkworld.{pkg}',
                'files': self._tree(d),
                'next': [f"m zkworld.{pkg}/vibe prompt='...'",
                         f'm zkworld.{pkg}/test']}

    def _starter_statement(self, fid, tid):
        try:
            return json.load(open(os.path.join(TEMPLATES, fid, tid, 'meta.json'))).get('statement')
        except Exception:  # noqa: BLE001
            return None

    def _new_custom(self, pkg, d, framework, desc=None):
        """A project in a catalog framework with no adapter (risc0, sp1,
        arkworks, o1js, ...): config.json carries the framework's runner
        preset, and the agent (vibe/port) writes the code behind it."""
        hit = [c for c in FW.CATALOG if c['id'] == framework]
        if not hit:
            return {'error': f'unknown framework {framework!r}',
                    'frameworks': [c['id'] for c in FW.CATALOG]}
        c = hit[0]
        cls = _pascal(pkg)
        os.makedirs(os.path.join(d, 'tests'), exist_ok=True)
        cases = os.path.join(d, 'tests', 'basic.json')
        if not os.path.exists(cases):
            self._emit(cases, '[]\n')
        self._emit(os.path.join(d, 'RUNNER.md'), (
            f'# {cls} — a {c["lang"]} ({framework}) project\n\n'
            f'zkworld drives this project through the runner protocol; `zk` in '
            f'config.json names the commands.\n\n'
            f'- `{" && ".join(c["preset"].get("build") or []) or "(no build)"}` builds it\n'
            f'- `{c["preset"]["run"]} --abi` prints '
            '`{"circuit", "inputs": [{"name", "public"}], "outputs": [{"name"}]}`\n'
            f'- `{c["preset"]["run"]} input.json` exits 0 and prints `{{"outputs": {{...}}}}` '
            'when the statement holds, non-zero (reason on stderr) when it does not\n'
            '- tests/*.json are the shared statement cases (see zkworld frameworks)\n\n'
            f'Install: {c.get("install", "")}\nDocs: {c["homepage"]}\n'))
        self._emit(os.path.join(d, '.gitignore'), 'target/\nnode_modules/\nbuild/\n')
        self._emit(os.path.join(d, 'config.json'), json.dumps({
            'name': pkg, 'title': cls,
            'description': desc or f'{cls} — a {c["lang"]} ({framework}) project.',
            'version': '0.1.0', 'submod_of': 'zkworld', 'framework': framework,
            'kind': f'{framework}-project', 'zk': {'lang': c['lang'], **c['preset']},
        }, indent=4) + '\n')
        self._as_mod(pkg, d)
        return {'name': pkg, 'path': d, 'framework': framework, 'via': 'custom',
                'mod': f'zkworld.{pkg}', 'files': self._tree(d),
                'next': [f"m zkworld.{pkg}/vibe prompt='...'  (writes the {framework} code + runner)"]}

    def _emit(self, path, content):
        with open(path, 'w') as f:
            f.write(content)

    # every contract dir is also a protocol mod: its own mod.py anchor (the
    # parent's fns bound to this contract), README and config anchor/fns
    SUB_FNS = ['forward', 'info', 'build', 'test', 'vibe', 'abi', 'tree',
               'read', 'write', 'clean', 'app', 'deploy', 'call', 'events']

    def _as_mod(self, pkg, d):
        meta = self._sub_meta(d)
        f = self._fw(d) or FW.get(None)
        desc = meta.get('description') or f'{_pascal(pkg)} — a {f.lang} project.'
        with open(os.path.join(TEMPLATES, 'mod.py.tmpl')) as fh:
            src = fh.read()
        src = (src.replace('{{PKG}}', pkg).replace('{{DESC_PY}}', repr(desc))
               .replace('{{LANG}}', f.lang))
        self._emit(os.path.join(d, 'mod.py'), src)
        readme = os.path.join(d, 'README.md')
        if not os.path.exists(readme):
            self._emit(readme, (
                f'# {pkg}\n\n{desc}\n\n'
                f'A {f.lang} project mod (framework `{f.id}`, submod of '
                f'`zkworld`), addressed `zkworld.{pkg}`. `{f.marker}` is its '
                f'manifest; build and test go through the zkworld {f.id} '
                f'adapter.\n\n```sh\nm zkworld.{pkg}/build\n'
                f'm zkworld.{pkg}/test\nm zkworld.{pkg}/vibe prompt="..."\n```\n'))
        fns = self.SUB_FNS if f.sandbox else [x for x in self.SUB_FNS
                                              if x not in ('deploy', 'call', 'events')] + ['witness']
        meta.update({'name': pkg, 'submod_of': 'zkworld', 'anchor': 'mod.py',
                     'framework': self._fid(d, f),
                     'fns': fns + [x for x in meta.get('ext_fns', []) if x not in fns]})
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
            f = self._fw(d)
            a = self.abi(c['name']) if c['built'] else {}
            abi = a.get('contracts', [])
            circuits = a.get('circuits', [])
            fns = [x for k in abi for x in k['functions']]
            srcs = [x for x in f.sources(d) if os.path.isfile(x) and x.endswith(f.ext)]
            tests = f.tests(d)
            lines = sum(len((self._read(x) or '').splitlines()) for x in srcs)
            touched = max((os.path.getmtime(x) for x in srcs + tests), default=0)
            dep = deps.get(c['name'])
            out.append({
                'name': c['name'], 'mod': f"zkworld.{c['name']}",
                'framework': c['framework'], 'statement': c.get('statement'),
                'title': meta.get('title') or _pascal(c['name']),
                'description': c['description'], 'template': c['template'],
                'forked_from': meta.get('forked_from'),
                'state': c['state'], 'lines': lines, 'tests': len(tests),
                'touched': touched,
                'reads': [x['name'] for x in fns if x['mutability'] == 'view'],
                'writes': [x['name'] for x in fns if x['mutability'] != 'view'],
                'events': [e['name'] for k in abi for e in k['events']],
                'sierra_felts': sum(k['sierra_felts'] for k in abi),
                'inputs': [i['name'] for k in circuits for i in k['inputs']],
                'outputs': [o['name'] for k in circuits for o in k['outputs']],
                'constraints': sum(k.get('constraints') or 0 for k in circuits) or None,
                'custom_app': os.path.isfile(os.path.join(d, 'app', 'index.html')),
                'deployed': ({'address': dep.get('address'), 'by': dep.get('by'),
                              'at': dep.get('at')} if dep else None),
                'url': f"{self.base}/{c['name']}/",
            })
        out.sort(key=lambda x: -x['touched'])
        return {'count': len(out), 'contracts': out,
                'templates': self.templates()['templates'],
                'frameworks': [x.describe() for x in FW.FRAMEWORKS.values()]}

    # ---- the fleet: who else on this box does zero-knowledge ----------------

    _fleet_cache = (0.0, None)
    # manifest file -> framework; circom has none, a dir of *.circom counts
    _FLEET_MARKERS = {'Scarb.toml': 'cairo', 'Nargo.toml': 'noir'}
    _FLEET_WORDS = re.compile(
        r'\b(cairo|starknet|scarb|snforge|sncast|katana|circom|snarkjs|groth16|'
        r'plonk|nargo|noir|halo2|risc0|sp1|zkvm|zk-?snark|zk-?stark|zokrates|'
        r'aleo|gnark|arkworks|plonky[23]|o1js|jolt|stwo)\b', re.I)

    def fleet(self, refresh=False):
        """Scan every mod on this box (orbit/ + core/) for a ZK setup in any
        framework zkworld knows.

        A mod is any dir with a config.json. Evidence, strongest first:
          contract  a Scarb.toml that builds a starknet contract
          project   a framework project (Scarb.toml, Nargo.toml, *.circom)
          mentions  only its config.json talks about a ZK toolchain
        Each project belongs to the nearest enclosing mod, so zkworld.counter's
        project is not also counted as zkworld's. Cached 60s."""
        at, hit = self.__class__._fleet_cache
        if hit is not None and not refresh and time.time() - at < 60:
            return hit
        t0 = time.time()
        top = os.path.dirname(os.path.dirname(ROOT))  # .../mod (orbit, core)
        skip = {'node_modules', 'target', '.git', 'releases', 'dist', 'out',
                '.next', '__pycache__', '.venv', 'venv', '.snfoundry_cache',
                '.sandbox', 'data', 'cache', '.cache', 'build', 'vendor'}
        words = self._FLEET_WORDS
        mods, projects, nfiles_at = {}, [], {}
        # only circuit-language extensions: .go / .rs files are not ZK by themselves
        exts = {f.ext: f.id for f in FW.FRAMEWORKS.values() if f.ext and f.kind == 'dsl'}
        by_marker = [f for f in FW.FRAMEWORKS.values()
                     if f.id not in ('circom', 'custom') and f.marker not in self._FLEET_MARKERS]
        for base in ('orbit', 'core'):
            for d, dirs, files in os.walk(os.path.join(top, base)):
                depth = os.path.relpath(d, top).count(os.sep)
                dirs[:] = [x for x in dirs if x not in skip and not x.startswith('.')
                           and depth < 7]
                if 'config.json' in files and d != os.path.join(top, base):
                    mods[d] = None
                for mk, fid in self._FLEET_MARKERS.items():
                    if mk in files:
                        projects.append((d, fid, mk))
                for f in by_marker:  # zokrates, leo, gnark, halo2 (marker + needle)
                    if f.marker in files and f.detect(d):
                        projects.append((d, f.id, f.marker))
                if any(f.endswith('.circom') for f in files) and not any(
                        d.startswith(p + os.sep) for p, k, _ in projects if k == 'circom'):
                    projects.append((d, 'circom', None))
                for f in files:
                    fid = exts.get(os.path.splitext(f)[1])
                    if fid:
                        nfiles_at.setdefault(d, {}).setdefault(fid, 0)
                        nfiles_at[d][fid] += 1

        def owner(p):  # nearest enclosing mod dir
            while p.startswith(top) and p != top:
                if p in mods:
                    return p
                p = os.path.dirname(p)
            return None

        def name_of(d):
            parts = os.path.relpath(d, top).split(os.sep)
            return '.'.join(x for x in parts if x not in ('orbit', 'core', 'src', 'mods')) or parts[-1]

        found = {}
        for d, fid, mk in projects:
            m = owner(d)
            if not m:
                continue
            man = self._read(os.path.join(d, mk)) or '' if mk else ''
            pkg = re.search(r'^\s*name\s*=\s*"([^"]+)"', man, re.M)
            ver = re.search(r'^\s*(?:cairo-version|compiler_version)\s*=\s*"([^"]+)"', man, re.M)
            if fid == 'circom':
                src = ''.join(self._read(x) or '' for x in glob.glob(os.path.join(d, '*.circom')))
                v = re.search(r'pragma\s+circom\s+([\d.]+)', src)
                ver, pkg = v, None
                tested = bool(glob.glob(os.path.join(d, 'tests', '*.json'))
                              or glob.glob(os.path.join(d, '**', '*.test.*'), recursive=True))
            elif fid == 'noir':
                tested = any('#[test' in (self._read(x) or '') for x in
                             glob.glob(os.path.join(d, 'src', '**', '*.nr'), recursive=True))
            elif fid == 'cairo':
                tested = 'snforge_std' in man or 'cairo_test' in man
            else:
                fw = FW.FRAMEWORKS[fid]
                tested = bool(fw.case_files(d) or fw.tests(d))
            found.setdefault(m, []).append({
                'path': os.path.relpath(d, m) if d != m else '.',
                'framework': fid,
                'package': pkg.group(1) if pkg else None,
                'version': ver.group(1) if ver else None,
                'contract': fid == 'cairo' and bool(
                    re.search(r'starknet-contract|^\s*starknet\s*=', man, re.M)),
                'tested': tested,
            })
        nfiles = {}
        for d, by in nfiles_at.items():
            m = owner(d)
            if m:
                for fid, n in by.items():
                    nfiles.setdefault(m, {}).setdefault(fid, 0)
                    nfiles[m][fid] += n

        # a mod whose projects live in nested mods (this workbench, starknet)
        inner = {}
        for m, ps in found.items():
            p = owner(os.path.dirname(m))
            while p:
                inner[p] = inner.get(p, 0) + len(ps)
                p = owner(os.path.dirname(p))

        out = []
        for d in mods:
            try:
                cfg = json.load(open(os.path.join(d, 'config.json')))
            except Exception:  # noqa: BLE001
                cfg = {}
            if not isinstance(cfg, dict):
                cfg = {}
            ps = found.get(d, [])
            keys = sorted(k for k, v in cfg.items()
                          if words.search(k) or words.search(json.dumps(v)))
            kw = sorted({w.lower() for w in words.findall(json.dumps(cfg))})
            if not ps and not keys:
                continue
            level = ('contract' if any(p['contract'] for p in ps)
                     else 'project' if ps else 'mentions')
            rel = os.path.relpath(d, top)
            name = name_of(d)
            out.append({
                'name': name,
                'path': rel,
                'title': cfg.get('title') or cfg.get('name') or name,
                'description': (cfg.get('description') or '')[:240] or None,
                'level': level,
                'frameworks': sorted({p['framework'] for p in ps}),
                'here': rel == 'orbit/zkworld' or rel.startswith('orbit/zkworld/'),
                'projects': ps,
                'files': nfiles.get(d, {}),
                'tested': any(p['tested'] for p in ps),
                'nested_projects': inner.get(d, 0),
                'keywords': kw,
                'config_keys': keys[:12],
                'port': cfg.get('port'),
                'url': (f"/{name.replace('.', '/')}/" if cfg.get('route') else None),
                'private': bool(cfg.get('private')),
            })
        rank = {'contract': 0, 'project': 1, 'mentions': 2}
        out.sort(key=lambda x: (rank[x['level']], -x['nested_projects'], x['here'], x['name']))
        res = {
            'count': len(out),
            'scanned': len(mods),
            'levels': {k: sum(x['level'] == k for x in out) for k in rank},
            'frameworks': {fid: sum(fid in x['frameworks'] for x in out)
                           for fid in FW.FRAMEWORKS},
            'mods': out,
            'took_ms': int((time.time() - t0) * 1000),
            'at': time.time(),
        }
        self.__class__._fleet_cache = (time.time(), res)
        return res

    def whitepaper(self):
        """The design paper (WHITEPAPER.md at the module root), as markdown."""
        p = os.path.join(ROOT, 'WHITEPAPER.md')
        return {'markdown': self._read(p) if os.path.exists(p) else None,
                'path': 'WHITEPAPER.md'}

    def fork(self, src, name, desc=None):
        """Copy a project into a new mod under a new name. The framework
        adapter renames what its toolchain keys on (Cairo: package, contract
        module, dispatchers, `use <pkg>::` paths; others: the manifest name)
        so the copy builds and tests on its own."""
        spkg, sd = self._contract(src)
        f = self._fw(sd)
        pkg, d = self._subdir(name)
        if os.path.exists(d):
            return {'error': f'{pkg!r} already exists'}
        shutil.copytree(sd, d, ignore=shutil.ignore_patterns(
            *f.clean_dirs, '__pycache__', 'Scarb.lock', 'mod.py', 'README.md'))
        f.rename(d, spkg, pkg)
        new = _pascal(pkg)
        meta = self._sub_meta(d)
        meta.update({'name': pkg, 'title': new, 'forked_from': spkg,
                     'framework': self._fid(sd, f),
                     'description': desc or f'{new} — forked from {spkg}.'})
        self._emit(os.path.join(d, 'config.json'), json.dumps(meta, indent=4) + '\n')
        self._as_mod(pkg, d)
        return {'name': pkg, 'contract': new, 'forked_from': spkg,
                'framework': f.id, 'mod': f'zkworld.{pkg}', 'files': self._tree(d)}

    def adopt(self):
        """Give every contract under src/mods/ the protocol files (mod.py,
        README, config anchor/fns) and pull any legacy contract still at the
        module root (orbit/zkworld/<name>/) into src/mods/."""
        moved = []
        for entry in sorted(os.listdir(ROOT)):
            d = os.path.join(ROOT, entry)
            if entry not in RESERVED and os.path.isdir(d) and self._is_submod(d):
                os.makedirs(MODS, exist_ok=True)
                shutil.move(d, os.path.join(MODS, entry))
                moved.append(entry)
        for c in self._catalog():
            self._as_mod(c['name'], c['path'])
        return {'moved': moved, 'mods': [f"zkworld.{c['name']}"
                                         for c in self._catalog()]}

    # ---- vibecode: hand one submod to the claudecode agent ------------------
    # For now vibe speaks the agent protocol (GET /agents + POST /run/stream,
    # SSE step/done/error) to orbit/claudecode on loopback, so the CLI spawn
    # spec, creds and cost metering live in ONE place. If that server is down
    # we fall back to spawning the CLI ourselves, same prompt, same scope.

    AGENT_URL = os.environ.get('ZKWORLD_AGENT_URL', 'http://127.0.0.1:51260')

    def _prompt(self, d):
        return (self._fw(d) or FW.get(None)).prompt

    def _agent_goal(self, d):
        # the agent server's PATH doesn't carry ~/.local/bin, so say where the
        # toolchain is, and pin the run to this one project dir
        f = self._fw(d) or FW.get(None)
        path = ':'.join(FW.EXTRA_BINS)
        zk = self._sub_meta(d).get('zk') if f.id == 'custom' else None
        return (f.prompt +
                (f"- This project is `{self._fid(d, f)}`; its config.json zk block: "
                 f"{json.dumps(zk)}. RUNNER.md says the same in prose.\n" if zk else '') +
                f"- Work ONLY inside {d}. Use absolute paths.\n"
                f"- The toolchain is on these dirs: run `export PATH={path}:$PATH` "
                f"first ({', '.join(f.tools)}).\n")

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
               '--append-system-prompt', self._prompt(d),
               '--permission-mode', 'acceptEdits',
               '--add-dir', d,
               '--output-format', 'json']
        if model:
            cmd += ['--model', str(model)]
        try:
            out = subprocess.run(cmd, cwd=d, capture_output=True, text=True,
                                 timeout=int(timeout), env=FW.env())
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

    def build(self, name, timeout=900):
        """Build one project with its framework's compiler (scarb build,
        circom, nargo compile). Returns the log, diagnostics pinned to
        file:line, and the ABI / circuit shape on success."""
        pkg, d = self._subdir(name)
        f = self._fw(d)
        if not f:
            return {'error': f'no such project {pkg!r}'}
        t0 = time.time()
        r = f.build(pkg, d, timeout)
        r['name'] = pkg
        r['framework'] = f.id
        r['seconds'] = round(time.time() - t0, 1)
        r.setdefault('diagnostics', [])
        return r

    def abi(self, name):
        """What a built project exposes. Cairo: contract classes (entrypoints,
        events, constructor, Sierra/CASM size). Circuits: `circuits` (inputs
        with public flag, outputs, constraint count). Build first."""
        pkg, d = self._subdir(name)
        f = self._fw(d)
        if not f:
            return {'name': pkg, 'contracts': [], 'error': f'no such project {pkg!r}'}
        return {**f.abi(pkg, d), 'framework': f.id}

    def witness(self, name, input=None):
        """Run a project on `input` {name: value} in whatever framework it is
        written: accepted + its outputs, or the constraint that rejected it.
        Names the program doesn't take are ignored. Open — it only computes."""
        pkg, d = self._contract(name)
        if isinstance(input, str):
            input = json.loads(input or '{}')

        # the `m` CLI hands dict args over with literal quotes kept
        # ({"'a'": "'5'"}); strip them so CLI and HTTP calls agree
        def unq(v):
            if isinstance(v, list):
                return [unq(x) for x in v]
            return v.strip('\'"') if isinstance(v, str) else v
        input = {unq(k): unq(v) for k, v in (input or {}).items()}
        try:
            # execute() drops names the program doesn't take, so one input
            # set (say {a, b, c}) runs against every port of a statement
            r = self._fw(d).execute(pkg, d, {'input': input or {}})
            r.pop('checked', None)
            if r.get('ok') and not r.get('holds'):
                r.update(ok=False, error=r.pop('why', 'computed a different value'))
            r.pop('holds', None)
            return {'name': pkg, **r}
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'error': str(e)}

    # ---- connect: one statement, many frameworks ----------------------------
    # Projects that prove the same thing carry the same `statement` in their
    # config.json (a starter's meta.json sets it; port() copies it). The
    # shared tests/*.json cases are the spec, so every port is held to the
    # same claims, and compare() runs one input through all of them.

    def statements(self):
        """Every statement with the projects (one per framework, or more)
        that implement it."""
        by = {}
        for c in self._catalog():
            st = c.get('statement')
            if st:
                by.setdefault(st, []).append({k: c[k] for k in ('name', 'framework', 'state')})
        return {'statements': [{'statement': k, 'frameworks': sorted({p['framework'] for p in v}),
                                'projects': v} for k, v in sorted(by.items())]}

    def compare(self, statement=None, names=None, input=None):
        """Run one input through every port of a statement (or the listed
        projects): accepted or rejected in each, with outputs and timing.
        `agree` = every built port gave the same verdict."""
        if isinstance(names, str):
            names = [x for x in re.split(r'[,\s]+', names) if x]
        if isinstance(input, str):
            input = json.loads(input or '{}')
        if not names:
            if not statement:
                return {'error': 'give statement= or names='}
            names = [c['name'] for c in self._catalog() if c.get('statement') == statement]
        rows = []
        for n in names:
            pkg, d = self._subdir(n)
            f = self._fw(d)
            if not f:
                rows.append({'name': pkg, 'ok': None, 'error': 'no such project'})
                continue
            if not f.runs:
                rows.append({'name': pkg, 'framework': f.id, 'ok': None,
                             'error': f'{f.id} has no executor (use its app)'})
                continue
            if f.build_state(d) == 'unbuilt':
                rows.append({'name': pkg, 'framework': self._fid(d, f), 'ok': None,
                             'error': 'not built'})
                continue
            t0 = time.time()
            try:
                r = f.execute(pkg, d, {'input': input or {}})
            except Exception as e:  # noqa: BLE001
                r = {'ok': False, 'error': str(e)}
            # ok = the claim holds: accepted AND any value it computes
            # matches the one given (circom computes c; noir takes it in)
            rows.append({'name': pkg, 'framework': self._fid(d, f), 'ok': r.get('holds', r.get('ok')),
                         'outputs': r.get('outputs'), 'error': r.get('error') or r.get('why'),
                         'ms': int((time.time() - t0) * 1000)})
        verdicts = {r['ok'] for r in rows if r.get('ok') is not None}
        return {'statement': statement, 'input': input or {}, 'rows': rows,
                'agree': len(verdicts) <= 1, 'verdict': (verdicts.pop() if len(verdicts) == 1 else None)}

    def port_to(self, src, framework, name=None, model=None, timeout=1800, on_step=None):
        """Re-implement project `src` in another framework. Makes the target
        (that framework's starter as a skeleton, or a runner-protocol project
        for catalog frameworks), copies src's tests/*.json over as the spec,
        then the agent rewrites it until build + every case passes. Both end
        up with the same `statement`, so compare() covers them."""
        spkg, sd = self._contract(src)
        sf = self._fw(sd)
        name = name or f'{spkg}_{framework.replace("-", "_")}'
        pkg, d = self._subdir(name)
        if self._is_submod(d):
            return {'error': f'{pkg!r} already exists'}
        made = self.new(pkg, framework=framework,
                        desc=f'{_pascal(spkg)} ported from {sf.id} to {framework}.')
        if made.get('error'):
            return made
        # the spec: src's statement cases replace the starter's
        for old in glob.glob(os.path.join(d, 'tests', '*.json')):
            os.remove(old)
        cases = sf.case_files(sd)
        os.makedirs(os.path.join(d, 'tests'), exist_ok=True)
        for c in cases:
            shutil.copy(c, os.path.join(d, 'tests', os.path.basename(c)))
        meta = self._sub_meta(sd)
        statement = meta.get('statement') or spkg
        if not meta.get('statement'):
            meta['statement'] = statement
            self._emit(os.path.join(sd, 'config.json'), json.dumps(meta, indent=4) + '\n')
        tm = self._sub_meta(d)
        tm.update(statement=statement, ported_from=spkg)
        self._emit(os.path.join(d, 'config.json'), json.dumps(tm, indent=4) + '\n')
        source = []
        for p in sf.sources(sd):
            rel = os.path.relpath(p, sd)
            if os.path.isfile(p) and '/target/' not in p and os.path.getsize(p) < 40000:
                source.append(f'--- {rel} ---\n{self._read(p)}')
        prompt = (
            f'Port this {sf.lang} ({sf.id}) project to {framework}. The skeleton in this '
            f'directory is a {framework} starter: REPLACE its logic with the statement below; '
            f'keep the toolchain files and the runner (if any) working.\n\n'
            f'The spec is tests/*.json (copied from the source project): the port must accept '
            f'every case without "fail" (and produce its `expect` values, which may be public '
            f'inputs rather than outputs in {framework}) and reject every case with "fail": true. '
            f'Keep input names identical to the source so the same cases run in both.'
            + ('' if cases else ' The source has no JSON cases: write tests/basic.json first, '
               'from its native tests.') +
            f'\n\nSource project {spkg}:\n\n' + '\n\n'.join(source)[:60000])
        res = self.vibe(pkg, prompt, model=model, timeout=timeout, on_step=on_step)
        res.update(ported_from=spkg, framework=framework, statement=statement,
                   compare=f'm zkworld/compare statement={statement} input=...')
        return res

    # ---- the app: a page per project (cairo: over a local devnet) -----------

    def _sandbox(self):
        # by path, same reason as mod.py itself: no bare-name imports here
        if not hasattr(self, '_sb'):
            spec = importlib.util.spec_from_file_location(
                'zkworld_sandbox', os.path.join(SRC, 'sandbox.py'))
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            self._sb = m
        return self._sb

    def _contract(self, name):
        pkg, d = self._subdir(name)
        if not self._is_submod(d):
            raise ValueError(f'no such project {pkg!r}')
        return pkg, d

    def _chain(self, d):
        """deploy/call/events need a framework with a sandbox chain."""
        f = self._fw(d)
        if not f.sandbox:
            return {'ok': False, 'error': f'{f.id} projects have no chain sandbox; '
                    'use witness() to run the circuit'}
        return None

    def app(self, name):
        """Everything a project's app page needs in one call. Cairo: ABI,
        sandbox deployment, accounts to act as. Circuits: their inputs and
        outputs (the page runs witness()). Plus whether the page is custom."""
        pkg, d = self._contract(name)
        f = self._fw(d)
        custom = os.path.isfile(os.path.join(d, 'app', 'index.html'))
        # app.json = the plain-language layer: labels, hidden inputs/outputs,
        # visuals. Optional; a broken one is reported, never fatal.
        ui, ui_error = {}, None
        try:
            with open(os.path.join(d, 'app.json')) as fh:
                ui = json.load(fh)
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            ui_error = f'app.json: {e}'
        base = {'name': pkg, 'title': _pascal(pkg), 'framework': self._fid(d, f),
                'description': self._sub_meta(d).get('description'),
                'url': f'{self.base}/{pkg}/', 'custom': custom,
                'state': f.build_state(d), 'ui': ui, 'ui_error': ui_error}
        if not f.sandbox:
            a = self.abi(pkg)
            return {**base, 'page': f'src/mods/{pkg}/app/index.html' if custom
                    else os.path.relpath(self.default_app(d), ROOT),
                    'abi': [], 'circuits': a.get('circuits', []),
                    'tests': [os.path.relpath(t, d) for t in f.tests(d)]}
        # opening the page boots the chain (~1s, reloads its dump), so the
        # deployment and accounts below are real, not "down"
        sb = self._sandbox()
        if f.build_state(d) != 'unbuilt':
            sb.start()
        return {**base, 'page': f'src/mods/{pkg}/app/index.html' if custom
                else 'src/templates/cairo/app.html',
                'abi': self.abi(pkg).get('contracts', []),
                'devnet': {'up': sb.alive(), 'rpc': sb.RPC + '/rpc'},
                'deployment': sb.deployment(pkg),
                'accounts': [{'name': a['name'], 'address': a['address']}
                             for a in sb.accounts()]}

    def deploy(self, name, args=None, contract=None, account='alice'):
        """Declare + deploy a built contract on the local devnet sandbox
        (started on demand). args = constructor args by name."""
        pkg, d = self._contract(name)
        if self._chain(d):
            return self._chain(d)
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
        if self._chain(d):
            return self._chain(d)
        try:
            return self._sandbox().call(pkg, d, fn, args, account)
        except Exception as e:  # noqa: BLE001
            return {'ok': False, 'error': str(e)}

    def events(self, name, limit=50):
        """Decoded events the deployed contract has emitted, newest first."""
        pkg, d = self._contract(name)
        if self._chain(d):
            return {'events': [], **self._chain(d)}
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
        """Test one project with its framework's runner (snforge test, circom
        witness cases, nargo test); no name = the module self-test."""
        if name is None:
            return self.status(deep=False)
        pkg, d = self._subdir(name)
        f = self._fw(d)
        if not f:
            return {'error': f'no such project {pkg!r}'}
        t0 = time.time()
        # test runners don't all emit the deployable artifact (snforge never
        # does), so build first when stale: the ABI and "built" state then
        # track what was tested, and a compile error surfaces with clean
        # diagnostics
        built = None
        if f.build_state(d) != 'fresh':
            built = self.build(pkg, timeout)
            if not built.get('ok'):
                built.update(name=pkg, tests=[], totals=None, stage='build')
                return built
        r = f.test(pkg, d, timeout)
        r['name'] = pkg
        r['framework'] = f.id
        r['seconds'] = round(time.time() - t0, 1)
        if built:
            r['artifacts'] = built.get('artifacts', [])
        return r

    def clean(self, name):
        """Remove a project's build output (target/, caches)."""
        pkg, d = self._subdir(name)
        f = self._fw(d)
        removed = []
        for sub in (f.clean_dirs if f else ('target',)):
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

    # ---- framework submods: one running service per framework --------------

    def _fw_mod(self, fid):
        """(Re)write src/mods/<fid>/ — mod.py from src/templates/fwmod.py.tmpl,
        config.json, README — so zkworld.<fid> is a mod of its own."""
        f, d = FW.get(fid), os.path.join(MODS, fid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(TEMPLATES, 'fwmod.py.tmpl')) as fh:
            src = fh.read()
        self._emit(os.path.join(d, 'mod.py'), src.replace('{{FID}}', fid)
                   .replace('{{TITLE}}', f.title).replace('{{PORT}}', str(FW_PORTS[fid])))
        fns = ['forward', 'info', 'health', 'toolchain', 'templates', 'projects',
               'new', 'build', 'test', 'vibe', 'clean', 'abi', 'witness', 'serve', 'kill']
        if fid == 'custom':
            fns.remove('new')
        self._emit(os.path.join(d, 'config.json'), json.dumps({
            'name': fid, 'title': f'{f.title} · zkworld',
            'description': f'The {f.title} framework as a running submod of zkworld: '
                           f'its toolchain, starters and every {f.title} project here. {f.blurb}',
            'version': self.config['version'], 'submod_of': 'zkworld',
            'kind': 'framework', 'framework_id': fid, 'anchor': 'mod.py',
            'server': '../../api/fwserver.py', 'port': FW_PORTS[fid],
            'host': '127.0.0.1', 'route': f"{self.base}/{fid}/",
            'fns': fns, 'write_fns': FW_WRITE_FNS,
        }, indent=4) + '\n')
        self._emit(os.path.join(d, '.gitignore'), 'server.log\n')
        self._emit(os.path.join(d, 'README.md'), (
            f'# zkworld.{fid}\n\nThe {f.title} framework as a running submod of '
            f'zkworld. {f.blurb}\n\nIts own server (`src/api/fwserver.py {fid} '
            f'{FW_PORTS[fid]}`, loopback) is kept up by the parent and proxied at '
            f'`{self.base}/{fid}/` (page) and `{self.base}/{fid}/api/<fn>`. It owns the '
            f'{f.title} projects: `projects()` lists them, and new/build/test/vibe/'
            f'witness only take those. Writes are owner-only (the parent gates '
            f'them).\n\n```sh\nm zkworld.{fid}/info\nm zkworld.{fid}/projects\n'
            f'm zkworld.{fid}/serve\n```\n'))
        return d

    def _listening(self, port):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.3):
                return True
        except OSError:
            return False

    def _fw_spawn(self, fid):
        d = os.path.join(MODS, fid)
        log = open(os.path.join(d, 'server.log'), 'a')
        p = subprocess.Popen([sys.executable, FW_SERVER, fid, str(FW_PORTS[fid])],
                             stdout=log, stderr=log, cwd=d, start_new_session=True)
        return p.pid

    def submods(self, action='status'):
        """The framework submods (zkworld.cairo, zkworld.noir, ...): port,
        running, project count. action=up starts the ones that are down (the
        server does this on boot and every 20s); restart kills and restarts
        all; sync rewrites their mod files. Projects are submods too; see list()."""
        if action not in ('status', 'up', 'restart', 'sync'):
            raise ValueError('action: status | up | restart | sync')
        started = []
        for fid in FW.FRAMEWORKS:
            d = os.path.join(MODS, fid)
            if action == 'sync' or not os.path.exists(os.path.join(d, 'mod.py')):
                self._fw_mod(fid)
            if action == 'restart' and self._listening(FW_PORTS[fid]):
                subprocess.run(['fuser', '-k', f'{FW_PORTS[fid]}/tcp'], capture_output=True)
                time.sleep(0.2)
            if action in ('up', 'restart') and not self._listening(FW_PORTS[fid]):
                started.append({'framework': fid, 'pid': self._fw_spawn(fid)})
        if started:
            time.sleep(1.0)
        projects = self._catalog()
        rows = []
        for fid, f in FW.FRAMEWORKS.items():
            mine = [c['name'] for c in projects if self._fw(c['path']) is f]
            rows.append({'framework': fid, 'mod': f'zkworld.{fid}', 'title': f.title,
                         'port': FW_PORTS[fid], 'running': self._listening(FW_PORTS[fid]),
                         'installed': f.installed(), 'url': f'{self.base}/{fid}/',
                         'projects': mine})
        return {'frameworks': rows, 'started': started,
                'running': sum(r['running'] for r in rows), 'total': len(rows),
                'projects': [f"zkworld.{c['name']}" for c in projects]}

    # ---- status / self-test -------------------------------------------------

    def status(self, deep=False):
        """Toolchain + catalog. deep=True builds and tests the examples."""
        s = {'module': 'zkworld', 'toolchain': self.toolchain(),
             'frameworks': {k: f.installed() for k, f in FW.FRAMEWORKS.items()},
             'contracts': self._catalog(),
             'submods': self.submods()['frameworks']}
        if deep:
            s['checks'] = {c['name']: {'build': self.build(c['name']),
                                       'test': self.test(c['name'])}
                           for c in self._catalog()}
        return s

    # ---- HTTP server (src/api/server.py) ------------------------------------

    def serve(self, port=None, background=True):
        """Run the API + console (src/api/server.py) on the module port:
        /zkworld = app, /zkworld/api/<fn> = protocol API, /zkworld/_api/<fn> = the
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
