"""
leanland — a reading partner that leaves definitions behind.

The problem it exists for: you read a paper, you want the thing it describes as
something you can run, and you end up with the same formula written three times
— once in a notebook, once in a service, once in the front end — drifting apart
from the day it is written, with nothing recording which equation of which paper
any of it came from.

So the library is the source of truth and everything else is lowered from it:

    lit/*.md     the papers, and what you decided about them
    lib/*.lean   the definitions — typed, cited, with the paper's own numbers
    out/         python + notebooks + a Rust backend + a Next.js surface + Lean

The LLM sits on the reading side, not the writing side. It discusses papers, it
drafts definitions — and a draft only becomes a definition after the parser, the
typechecker and the source's own numbers accept it. `parity` then runs every
example through the reference interpreter and every generated target, so "the
same formula in four places" is a checked claim rather than a hope.

Mod protocol: a null call returns info; the console and the JSON API share one
port (50540, loopback by default).

CLI:
    m leanland                              # what is in the library
    m leanland/defs                         # every definition, with its citation
    m leanland/show kelly                   # one def: source, examples, lowerings
    m leanland/verify                       # typecheck + run every #example
    m leanland/parity                       # reference vs python vs rust vs js
    m leanland/build                        # regenerate out/
    m leanland/drift                        # has anyone edited generated code?
    m leanland/lower kelly target=rust      # see one def in one language

    m leanland/papers                       # the literature
    m leanland/paper kelly1956              # one entry
    m leanland/arxiv 1706.03762             # pull metadata off arXiv
    m leanland/read kelly1956               # draft the reading note

    m leanland/discuss "why not full Kelly?"
    m leanland/elaborate "half-Kelly staking" paper=thorp2006
    m leanland/add "def double (x : Real) : Real := 2 * x"

    m leanland/serve                        # console + API on :50540
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import mod as m

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 50540
LOG_DIR = '/tmp/leanland'

if ROOT not in sys.path:                    # so `src` imports work under any anchor
    sys.path.insert(0, os.path.dirname(ROOT))


class Mod:
    description = ('leanland — an LLM you read literature with that leaves behind a lean, '
                   'cited library of definitions, lowered into Python notebooks, a Rust '
                   'backend and a Next.js surface; four-way parity checks that the one '
                   'source of truth really is one')

    def __init__(self, path: str = None):
        self.root = m.abspath(path) if path else ROOT

    # -- plumbing ----------------------------------------------------------

    @property
    def lib(self):
        from leanland.src.library import Library
        return Library(self.root)

    @staticmethod
    def _mod(name):
        from leanland.src import api, chat, check, ir, lean, lower, library, lit
        return {'api': api, 'chat': chat, 'check': check, 'ir': ir, 'lean': lean,
                'lower': lower, 'library': library, 'lit': lit}[name]

    def forward(self, **kwargs):
        return self.info()

    def info(self) -> dict:
        lib = self.lib
        defs, errors = lib.load()
        v = lib.verify()
        return {
            'name': 'leanland',
            'description': self.description,
            'root': self.root,
            'library': {'files': lib.files(), 'defs': sorted(defs),
                        'examples': v['examples'], 'ok': v['ok'], 'errors': errors},
            'literature': lib.lit.keys(),
            'flags': {k: v[k] for k in ('unsourced', 'missing_lit', 'untested')},
            'targets': ['python', 'notebook', 'rust', 'nextjs', 'lean'],
            'url': f'http://localhost:{PORT}',
        }

    # -- the library -------------------------------------------------------

    def defs(self) -> list:
        return self.lib.index()

    def show(self, name: str) -> dict:
        lib = self.lib
        d = lib.get(name)
        all_defs, _ = lib.load()
        from leanland.src import lower
        out = {**d.to_dict(), 'cases': lower.cases(d, all_defs)}
        if d.source.get('key') in lib.lit.keys():
            out['paper'] = dict(lib.lit.get(d.source['key']))
        return out

    def add(self, source: str, file: str = 'user.lean', replace: bool = True) -> dict:
        """Typecheck a block of .lean source and file it. Nothing is written unless
        it parses, typechecks, and reproduces the numbers its #examples claim."""
        return self.lib.add(source, file=file, replace=bool(replace))

    def rm(self, name: str) -> dict:
        return self.lib.rm(name)

    def lower(self, name: str = None, target: str = 'python') -> str:
        """One def (or the whole library) in one target language."""
        from leanland.src import lower as L
        lib = self.lib
        defs, _ = lib.load()
        picked = L.closure(defs, [name]) if name else defs
        if target in ('lean', 'lean4'):
            return L.lean4(picked)
        if target in ('notebook', 'ipynb'):
            return json.dumps(L.notebook(lib.get(name), defs, lib.lit.all()), indent=1)
        fn = {'python': L.python, 'py': L.python, 'rust': L.rust, 'rs': L.rust,
              'typescript': L.typescript, 'ts': L.typescript}.get(target)
        if fn is None:
            raise ValueError(f'unknown target {target!r}: python|rust|typescript|lean|notebook')
        return fn(picked)

    # -- checking ----------------------------------------------------------

    def verify(self) -> dict:
        """Typecheck everything and run every #example through the reference
        interpreter. Also reports what nothing is checking."""
        return self.lib.verify()

    def parity(self, targets: str = None, only: str = None) -> dict:
        """Run every #example on every target with a toolchain here and compare
        them to the reference interpreter."""
        from leanland.src import check
        defs, _ = self.lib.load()
        r = check.parity(defs,
                         targets=targets.split(',') if targets else None,
                         only=only.split(',') if only else None)
        return {k: r[k] for k in ('ok', 'cases', 'targets', 'skipped',
                                  'worst_delta', 'mismatches')}

    def build(self, targets: str = None) -> dict:
        """Regenerate out/ — python, notebooks, the Rust crate, the Next.js tree, Lean."""
        return self.lib.build(targets.split(',') if targets else None)

    def drift(self) -> dict:
        """Generated files edited by hand, or left stale. Both mean out/ stopped
        being a shadow of lib/."""
        return self.lib.drift()

    def test(self) -> dict:
        """The module's own gate: the library verifies, and every target agrees."""
        v = self.verify()
        p = self.parity()
        ok = v['ok'] and p['ok']
        return {'ok': ok, 'verify': {k: v[k] for k in ('defs', 'examples', 'ok', 'errors')},
                'parity': p}

    # -- literature --------------------------------------------------------

    def papers(self, q: str = '') -> list:
        lit = self.lib.lit
        return [dict(p) for p in (lit.search(q) if q else lit.all().values())]

    def paper(self, key: str) -> dict:
        return dict(self.lib.lit.get(key))

    def lit_add(self, key: str, title: str = '', authors: str = '', year=None,
                url: str = '', notes: str = '', **kwargs) -> dict:
        return dict(self.lib.lit.add(key, title=title, authors=authors, year=year,
                                     url=url, notes=notes, **kwargs))

    def arxiv(self, id: str, key: str = None) -> dict:
        """Pull a paper's metadata off arXiv into lit/."""
        return dict(self.lib.lit.add_arxiv(id, key=key))

    def note(self, key: str, text: str) -> dict:
        return dict(self.lib.lit.note(key, text))

    def lit_rm(self, key: str) -> dict:
        return self.lib.lit.rm(key)

    # -- the agent ---------------------------------------------------------

    def discuss(self, message: str, paper: str = None, model: str = None,
                full: bool = False) -> str:
        """Talk about the literature, grounded in lit/ and in what is already defined."""
        from leanland.src import chat
        return chat.discuss(self.lib, message, paper=paper, model=model,
                            full=bool(full))['answer']

    def elaborate(self, want: str, paper: str = None, tries: int = 3,
                  model: str = None, file: str = None, write: bool = True) -> dict:
        """Draft a definition and keep redrafting until the compiler accepts it.

        The model proposes; the parser, the typechecker and the source's own
        numbers dispose. Nothing reaches lib/ that did not pass all three."""
        from leanland.src import chat
        return chat.elaborate(self.lib, want, paper=paper, tries=int(tries),
                              model=model, file=file, write=bool(write))

    def read(self, key: str, about: str = '', model: str = None) -> dict:
        """Draft the reading note for a paper, appended to lit/<key>.md."""
        from leanland.src import chat
        return chat.read(self.lib, key, about=about, model=model)

    # -- serving -----------------------------------------------------------

    def serve(self, port: int = PORT, host: str = '127.0.0.1', background: bool = True):
        """Console + JSON API on one port. Loopback by default — see README before
        putting it on 0.0.0.0."""
        port = int(port)
        if not background:
            from leanland.src import api
            return api.serve(port=port, host=host, root=self.root)
        self.kill(port)
        os.makedirs(LOG_DIR, exist_ok=True)
        logf = open(os.path.join(LOG_DIR, 'app.log'), 'w')
        env = dict(os.environ)
        parent = os.path.dirname(os.path.dirname(self.root))     # …/mod
        env['PYTHONPATH'] = parent + (':' + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        proc = subprocess.Popen(
            [sys.executable, '-c',
             f'import mod as m; m.mod("leanland")({self.root!r})'
             f'.serve(port={port}, host={host!r}, background=False)'],
            stdout=logf, stderr=subprocess.STDOUT, env=env, start_new_session=True)
        with open(os.path.join(LOG_DIR, 'app.pid'), 'w') as f:
            f.write(str(proc.pid))
        return {'running': self._wait(port), 'pid': proc.pid,
                'url': f'http://{host}:{port}', 'log': os.path.join(LOG_DIR, 'app.log')}

    @staticmethod
    def _wait(port, timeout=25) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health',
                                            timeout=2) as r:
                    if r.status == 200:
                        return True
            except Exception:
                time.sleep(0.4)
        return False

    def kill(self, port: int = PORT) -> dict:
        try:
            m.kill_port(int(port))
        except Exception:
            subprocess.run(['bash', '-c', f'fuser -k {int(port)}/tcp'],
                           capture_output=True)
        return {'killed': int(port)}

    def status(self) -> dict:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/api/health', timeout=2) as r:
                return {'running': True, **json.loads(r.read())}
        except Exception as e:
            return {'running': False, 'error': str(e)}

    def logs(self, n: int = 50) -> str:
        path = os.path.join(LOG_DIR, 'app.log')
        if not os.path.exists(path):
            return 'no log yet'
        with open(path) as f:
            return ''.join(f.readlines()[-int(n):])
