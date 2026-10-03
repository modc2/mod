"""
The library: `lib/*.lean` on disk, typechecked, and lowered into `out/`.

Load is strict about one thing and lenient about another. Strict: a file that
does not typecheck does not enter the library — a half-elaborated definition is
worse than no definition, because everything downstream would still generate.
Lenient: one bad file does not stop the others loading; you get the library you
have plus a list of what is broken, which is what you want while you are in the
middle of writing one.
"""
from __future__ import annotations

import hashlib
import json
import os

from . import ir, lean, lower
from .ir import Def
from .lit import Lit

LIB_HEADER = '''-- {name}
--
-- The source of truth. Everything under out/ is lowered from these definitions;
-- editing the generated code is editing a shadow.
'''


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class Library:
    def __init__(self, root: str):
        self.root = root
        self.lib_dir = os.path.join(root, 'lib')
        self.out_dir = os.path.join(root, 'out')
        self.lit = Lit(os.path.join(root, 'lit'))
        os.makedirs(self.lib_dir, exist_ok=True)
        self._cache = None

    # -- loading -----------------------------------------------------------

    def files(self) -> list[str]:
        return sorted(f for f in os.listdir(self.lib_dir) if f.endswith('.lean'))

    def load(self, refresh: bool = True) -> tuple[dict[str, Def], list[dict]]:
        """Every def that parses *and* typechecks, plus the errors for the rest."""
        if self._cache and not refresh:
            return self._cache
        defs: dict[str, Def] = {}
        errors: list[dict] = []
        pending: list[tuple[str, Def]] = []
        for fn in self.files():
            path = os.path.join(self.lib_dir, fn)
            with open(path) as f:
                src = f.read()
            try:
                for d in lean.parse(src, fn):
                    if d.name in dict(pending) or d.name in defs:
                        errors.append({'file': fn, 'def': d.name,
                                       'error': f"'{d.name}' is defined twice"})
                        continue
                    pending.append((fn, d))
            except SyntaxError as e:
                errors.append({'file': fn, 'error': str(e)})

        # Typecheck in dependency order, which the files are not obliged to be
        # in: keep making passes over what is left until a pass adds nothing.
        # Whatever remains is either circular or depends on a def that failed.
        remaining = list(pending)
        while remaining:
            ready = [(fn, d) for fn, d in remaining if all(dep in defs for dep in d.deps)]
            if not ready:
                for fn, d in remaining:
                    missing = ', '.join(dep for dep in d.deps if dep not in defs)
                    errors.append({'file': fn, 'def': d.name,
                                   'error': f'unresolved dependency: {missing}'})
                break
            for fn, d in ready:
                try:
                    ir.check(d, defs)
                    defs[d.name] = d
                except TypeError as e:
                    errors.append({'file': fn, 'def': d.name, 'error': str(e)})
                remaining.remove((fn, d))
        self._cache = (defs, errors)
        return self._cache

    @property
    def defs(self) -> dict[str, Def]:
        return self.load(refresh=True)[0]

    def get(self, name: str) -> Def:
        defs = self.defs
        if name not in defs:
            raise KeyError(f"no definition named '{name}' "
                           f"(have: {', '.join(sorted(defs)) or 'none'})")
        return defs[name]

    def index(self) -> list[dict]:
        defs = self.defs
        out = []
        for d in lower.order(defs):
            out.append({'name': d.name, 'signature': d.signature(), 'ret': d.ret,
                        'params': d.params, 'doc': d.doc, 'source': d.source,
                        'deps': d.deps, 'examples': len(d.examples), 'lean': d.lean})
        return out

    # -- writing -----------------------------------------------------------

    def add(self, source: str, file: str = 'user.lean', replace: bool = True) -> dict:
        """Typecheck a block of .lean source against the library, then file it.

        Nothing is written until it typechecks. This is the gate the LLM
        elaborator goes through: the model proposes surface syntax, the compiler
        decides whether it is a definition.
        """
        if not file.endswith('.lean'):
            file += '.lean'
        defs, _ = self.load()
        try:
            new = lean.parse(source, file)
        except SyntaxError as e:
            return {'ok': False, 'stage': 'parse', 'error': str(e)}
        env = dict(defs)
        checked = []
        for d in new:
            if d.name in defs and not replace:
                return {'ok': False, 'stage': 'check',
                        'error': f"'{d.name}' already exists (pass replace=1 to overwrite)"}
            try:
                ir.check(d, env)
            except TypeError as e:
                return {'ok': False, 'stage': 'check', 'def': d.name, 'error': str(e)}
            env[d.name] = d
            checked.append(d)
        failed = [r for r in (self.verify_def(d, env) for d in checked) if not r['ok']]
        if failed:
            return {'ok': False, 'stage': 'examples', 'failures': failed}

        path = os.path.join(self.lib_dir, file)
        head = '' if os.path.exists(path) else LIB_HEADER.format(name=file)
        existing = open(path).read() if os.path.exists(path) else ''
        keep = self._strip_defs(existing, [d.name for d in checked]) if replace else existing
        with open(path, 'w') as f:
            f.write((head + keep).rstrip() + '\n\n' + source.strip() + '\n')
        self._cache = None
        return {'ok': True, 'file': file, 'defs': [d.name for d in checked],
                'examples': sum(len(d.examples) for d in checked)}

    @staticmethod
    def _strip_defs(text: str, names: list[str]) -> str:
        """Drop the whole text block of the named defs — doc comment, attributes,
        body and #examples — so `add` replaces rather than duplicates."""
        lines = text.splitlines()
        starts = [i for i, l in enumerate(lines) if l.strip().startswith('def ')]
        blocks = []                                      # (start, end, name)
        for n, i in enumerate(starts):
            top, in_doc = i, False                       # pull the doc/attr block up with it
            while top > 0:
                prev = lines[top - 1].strip()
                if in_doc:
                    top -= 1
                    in_doc = not prev.startswith('/--')
                elif prev.startswith('@[') or prev.startswith('--') or (
                        prev.startswith('/--') and prev.endswith('-/')):
                    top -= 1
                elif prev.endswith('-/'):                # closing line of a multi-line doc
                    top -= 1
                    in_doc = True
                else:
                    break
            end = starts[n + 1] if n + 1 < len(starts) else len(lines)
            blocks.append((top, end, lines[i].strip().split()[1].split('(')[0]))
        drop = set()
        for top, end, name in blocks:
            if name in names:
                drop.update(range(top, end))
        kept = [l for i, l in enumerate(lines) if i not in drop]
        out = '\n'.join(kept).rstrip()
        return out + '\n' if out else ''

    def rm(self, name: str) -> dict:
        d = self.get(name)
        users = [o.name for o in self.defs.values() if name in o.deps]
        if users:
            raise ValueError(f"'{name}' is used by {', '.join(users)}")
        for fn in self.files():
            path = os.path.join(self.lib_dir, fn)
            text = open(path).read()
            if f'def {name}' in text:
                open(path, 'w').write(self._strip_defs(text, [name]))
        self._cache = None
        return {'removed': name, 'was': d.signature()}

    # -- checking ----------------------------------------------------------

    def verify_def(self, d: Def, defs: dict[str, Def]) -> dict:
        """Run a def's #examples through the reference interpreter."""
        results = []
        for ex in d.examples:
            args = [ir.evaluate(a, {}, defs) for a in ex['args']]
            want = ir.evaluate(ex['expect'], {}, defs)
            try:
                got = ir.call(d, args, defs)
                ok = (got == want if isinstance(want, bool)
                      else abs(got - want) <= ex['tol'])
                results.append({'args': args, 'expect': want, 'got': got, 'ok': ok})
            except Exception as e:
                results.append({'args': args, 'expect': want, 'error': str(e), 'ok': False})
        return {'def': d.name, 'ok': all(r['ok'] for r in results),
                'cases': len(results), 'results': [r for r in results if not r['ok']] or results}

    def verify(self) -> dict:
        defs, errors = self.load()
        checks = [self.verify_def(d, defs) for d in lower.order(defs)]
        unsourced = [d.name for d in defs.values()
                     if not d.source.get('key') and not d.source.get('convention')]
        missing_lit = sorted({d.source['key'] for d in defs.values()
                              if d.source.get('key')} - set(self.lit.keys()))
        untested = [d.name for d in defs.values() if not d.examples]
        return {
            'defs': len(defs), 'errors': errors,
            'examples': sum(c['cases'] for c in checks),
            'failed': [c for c in checks if not c['ok']],
            'ok': not errors and all(c['ok'] for c in checks),
            'unsourced': unsourced,          # a def nobody can trace back to a paper
            'conventions': [d.name for d in defs.values() if d.source.get('convention')],
            'missing_lit': missing_lit,      # cites a key with no lit/ entry
            'untested': untested,            # no number from the source to check against
        }

    # -- lowering ----------------------------------------------------------

    def artifacts(self, targets=None) -> dict[str, str]:
        """Every generated file, as {relative path: content}. Pure — writes nothing."""
        defs, _ = self.load()
        if not defs:
            return {}
        papers = {k: dict(v) for k, v in self.lit.all().items()}
        want = set(targets or ('python', 'notebook', 'rust', 'nextjs', 'lean'))
        files: dict[str, str] = {}
        if 'python' in want:
            files['python/leanland_lib.py'] = lower.python(defs)
            files['python/test_leanland_lib.py'] = lower.python_tests(defs)
        if 'rust' in want:
            for p, c in lower.rust_crate(defs).items():
                files[f'rust/{p}'] = c
        if 'nextjs' in want:
            for p, c in lower.nextjs(defs).items():
                files[f'nextjs/{p}'] = c
        if 'lean' in want:
            files['lean/Leanland.lean'] = lower.lean4(defs)
        if 'notebook' in want:
            for d in lower.order(defs):
                files[f'notebooks/{d.name}.ipynb'] = json.dumps(
                    lower.notebook(d, defs, papers), indent=1) + '\n'
            files['notebooks/_library.ipynb'] = json.dumps(
                lower.index_notebook(defs, papers), indent=1) + '\n'
        return files

    def build(self, targets=None) -> dict:
        files = self.artifacts(targets)
        written = []
        for rel, content in files.items():
            path = os.path.join(self.out_dir, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if os.path.exists(path) and open(path).read() == content:
                continue
            with open(path, 'w') as f:
                f.write(content)
            written.append(rel)
        targets = sorted(set(targets or ('python', 'notebook', 'rust', 'nextjs', 'lean')))
        manifest = {'targets': targets, 'defs': sorted(self.load()[0]),
                    'files': {r: _sha(c) for r, c in files.items()}}
        os.makedirs(self.out_dir, exist_ok=True)
        with open(os.path.join(self.out_dir, 'manifest.json'), 'w') as f:
            json.dump(manifest, f, indent=2)
        return {'out': self.out_dir, 'files': len(files), 'written': written,
                'targets': targets}

    def drift(self) -> dict:
        """Generated files that were edited by hand, and generated files that are
        stale. Both mean the same thing: something downstream stopped being a
        shadow of the library."""
        man_path = os.path.join(self.out_dir, 'manifest.json')
        if not os.path.exists(man_path):
            return {'clean': True, 'built': False, 'edited': [], 'stale': [],
                    'missing': [], 'orphaned': [],
                    'note': 'nothing has been built yet — run build'}
        manifest = json.load(open(man_path))
        man = manifest['files']
        # Only the targets that were actually built are in scope: a notebook that
        # was never generated is not drift, it is a target you did not ask for.
        files = self.artifacts(manifest.get('targets'))
        edited, stale, missing = [], [], []
        for rel, content in files.items():
            path = os.path.join(self.out_dir, rel)
            if not os.path.exists(path):
                missing.append(rel)
                continue
            on_disk = open(path).read()
            if on_disk == content:
                continue
            (edited if man.get(rel) and _sha(on_disk) != man[rel] else stale).append(rel)
        orphaned = [rel for rel in man if rel not in files]
        return {'clean': not (edited or stale or missing or orphaned), 'built': True,
                'targets': manifest.get('targets'), 'edited': edited, 'stale': stale,
                'missing': missing, 'orphaned': orphaned}
