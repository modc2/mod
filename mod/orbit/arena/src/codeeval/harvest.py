"""harvest.py — extract coding reconstruction tasks from the orbit mod repo.

Walks every Python file in orbit, finds pure deterministic functions (or
sampled stochastic ones), generates test vectors, and writes a JSON task bank
that coderecon.py embeds directly in its class source.

Usage:
    python harvest.py [--orbit /path/to/orbit] [--out tasks.json] [--n 50]
    python harvest.py --generate coderecon.py  # writes the game class too
"""

import ast
import importlib.util
import inspect
import json
import os
import random
import re
import sys
import textwrap
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── config ────────────────────────────────────────────────────────────────────

ORBIT_DIR = Path(__file__).resolve().parents[3]  # orbit/
MAX_TASKS = 60
MAX_TESTS_PER_TASK = 12
STOCHASTIC_SEEDS = 8   # seeds to sample for probabilistic functions
MAX_CONTEXT_LINES = 40  # max lines of context to include per task

# Imports that disqualify a module from harvesting
UNSAFE_IMPORTS = {
    'requests', 'urllib', 'http', 'socket', 'subprocess', 'multiprocessing',
    'threading', 'asyncio', 'aiohttp', 'flask', 'fastapi', 'django',
    'sqlalchemy', 'psycopg2', 'pymongo', 'redis',
    'boto3', 'google', 'azure',
    'torch', 'tensorflow', 'keras',
    'cv2', 'PIL', 'matplotlib', 'numpy', 'pandas', 'scipy',
}

# Names in a function body that signal impurity
IMPURE_NAMES = {
    'open', 'print', 'input', 'exit', 'quit',
    '__import__', 'importlib', 'exec', 'eval', 'compile',
    'globals', 'locals', 'vars', 'dir',
}

# Attribute calls that signal impurity (object.method patterns)
IMPURE_ATTRS = {
    'os', 'sys', 'subprocess', 'socket', 'requests', 'urllib',
    'logging', 'logger', 'print',
}

# Attribute calls that signal stochasticity
STOCHASTIC_ATTRS = {'random', 'rand', 'choice', 'shuffle', 'sample', 'randint', 'uniform'}


# ── AST analysis ──────────────────────────────────────────────────────────────

def _collect_imports(tree: ast.Module) -> set:
    """Top-level import names in a module."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split('.')[0])
    return names


def _body_uses(fn: ast.FunctionDef, attr_set: set) -> bool:
    """Does the function body reference any of these attribute/name patterns?"""
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id in attr_set:
            return True
        if isinstance(node, ast.Attribute) and node.attr in attr_set:
            return True
    return False


def _is_stochastic(fn: ast.FunctionDef) -> bool:
    return _body_uses(fn, STOCHASTIC_ATTRS)


def _is_impure(fn: ast.FunctionDef) -> bool:
    return _body_uses(fn, IMPURE_NAMES | IMPURE_ATTRS)


def _get_fn_source(source: str, fn: ast.FunctionDef) -> str:
    lines = source.splitlines()
    return textwrap.dedent('\n'.join(lines[fn.lineno - 1:fn.end_lineno]))


def _make_stub(fn: ast.FunctionDef, source: str) -> str:
    """Return the function with its body replaced by `pass`.

    Keeps: decorators (if any), the def signature, and the docstring (if any).
    Replaces: the rest of the body with a single `pass`.
    """
    lines = source.splitlines()

    # Determine the last line to keep (signature + optional docstring)
    keep_through = fn.lineno - 1  # 0-indexed last line to keep, start at def

    # Signature may span multiple lines (fn.lineno → first body statement)
    if fn.body:
        # The signature ends just before the first body statement
        keep_through = fn.body[0].lineno - 2  # 0-indexed

        # If the first body node is a docstring, include it too
        first = fn.body[0]
        if (isinstance(first, ast.Expr) and
                isinstance(first.value, ast.Constant) and
                isinstance(first.value.value, str)):
            keep_through = first.end_lineno - 1  # 0-indexed, inclusive

    kept = lines[fn.lineno - 1:keep_through + 1]
    indent = ' ' * (fn.col_offset + 4)
    return '\n'.join(kept) + f'\n{indent}pass'


def _context_for(path: Path, fn: ast.FunctionDef, tree: ast.Module, source: str) -> str:
    """Safe imports + any class/constants needed by this function."""
    lines = []
    # Safe stdlib imports from the module
    safe_stdlib = {
        'math', 'itertools', 'functools', 'collections', 'string', 'operator',
        'heapq', 'bisect', 'copy', 'enum', 'dataclasses', 'typing',
        're', 'json', 'decimal', 'fractions', 'statistics',
    }
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split('.')[0]
                if root in safe_stdlib:
                    name = alias.asname or alias.name
                    lines.append(f'import {alias.name}' + (f' as {alias.asname}' if alias.asname else ''))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split('.')[0]
                if root in safe_stdlib:
                    names = ', '.join(
                        (a.name + (f' as {a.asname}' if a.asname else '')) for a in node.names)
                    lines.append(f'from {node.module} import {names}')
    # Module-level constants and simple assignments
    for node in tree.body:
        if isinstance(node, ast.Assign):
            src_lines = source.splitlines()
            snippet = '\n'.join(src_lines[node.lineno - 1:node.end_lineno])
            if len(snippet) < 200:
                lines.append(snippet)
    return '\n'.join(lines[:MAX_CONTEXT_LINES])


# ── test case generation ──────────────────────────────────────────────────────

def _is_method(fn: ast.FunctionDef) -> bool:
    """True if the first parameter is self or cls (i.e., this is a method)."""
    return bool(fn.args.args and fn.args.args[0].arg in ('self', 'cls'))


def _arg_count(fn: ast.FunctionDef) -> int:
    """Number of positional parameters (excluding self/cls)."""
    args = fn.args.args
    skip = 1 if (args and args[0].arg in ('self', 'cls')) else 0
    return len(args) - skip


def _gen_inputs(fn: ast.FunctionDef, rng: random.Random) -> List[Any]:
    """Heuristic input generation based on parameter names and annotations."""
    n = _arg_count(fn)
    args = fn.args.args
    if args and args[0].arg in ('self', 'cls'):
        args = args[1:]

    result = []
    for arg in args:
        name = arg.arg.lower()
        ann = arg.annotation
        ann_name = ''
        if ann:
            if isinstance(ann, ast.Name):
                ann_name = ann.id.lower()
            elif isinstance(ann, ast.Constant):
                ann_name = str(ann.value).lower()

        # Guess by annotation or name
        if ann_name in ('int', 'integer') or any(k in name for k in ('n', 'num', 'count', 'size', 'k', 'i', 'j', 'idx', 'index')):
            result.append(rng.randint(0, 20))
        elif ann_name in ('float',) or any(k in name for k in ('x', 'y', 'z', 'val', 'value', 'rate', 'prob', 'weight')):
            result.append(round(rng.uniform(0.0, 10.0), 2))
        elif ann_name in ('str', 'string') or any(k in name for k in ('s', 'text', 'name', 'key', 'word', 'prefix', 'suffix', 'sep', 'delim')):
            words = ['hello', 'world', 'foo', 'bar', 'baz', 'abc', 'xyz', '123']
            result.append(rng.choice(words))
        elif ann_name in ('list',) or any(k in name for k in ('lst', 'arr', 'items', 'data', 'seq', 'nums', 'vals', 'elements')):
            result.append([rng.randint(0, 100) for _ in range(rng.randint(2, 6))])
        elif ann_name in ('dict',) or any(k in name for k in ('d', 'mapping', 'config', 'params', 'opts')):
            result.append({f'k{i}': rng.randint(0, 10) for i in range(rng.randint(1, 3))})
        elif ann_name in ('bool',) or any(k in name for k in ('flag', 'enabled', 'active', 'reverse', 'ascending')):
            result.append(rng.choice([True, False]))
        else:
            # Fallback: try a small int
            result.append(rng.randint(0, 10))
    return result


def _run_fn(fn_source: str, context: str, fn_name: str,
            args: List[Any], kwargs: Dict[str, Any],
            seed: Optional[int] = None) -> Tuple[bool, Any, str]:
    """Execute fn in an isolated namespace. Returns (ok, result, error)."""
    ns = {}
    if seed is not None:
        rng_setup = f'import random as _rng; _rng.seed({seed})\n'
    else:
        rng_setup = ''
    try:
        exec(rng_setup + context + '\n' + fn_source, ns)
    except Exception as e:
        return False, None, f'context exec failed: {e}'
    fn = ns.get(fn_name)
    if fn is None:
        return False, None, f'function {fn_name!r} not found after exec'
    try:
        result = fn(*args, **kwargs)
        # Only keep JSON-serializable results
        json.dumps(result)
        return True, result, ''
    except (TypeError, ValueError) as e:
        return False, None, f'result not serializable: {e}'
    except Exception as e:
        return False, None, f'call failed: {e}'


def _make_tests(fn: ast.FunctionDef, fn_source: str, context: str,
                stochastic: bool, rng: random.Random) -> List[Dict]:
    """Generate test vectors by running the original function."""
    tests = []
    seen = set()
    n_arg = _arg_count(fn)
    fn_name = fn.name

    attempts = 0
    while len(tests) < MAX_TESTS_PER_TASK and attempts < MAX_TESTS_PER_TASK * 5:
        attempts += 1
        args = _gen_inputs(fn, rng)
        if len(args) != n_arg:
            continue
        key = json.dumps(args, sort_keys=True, default=str)
        if key in seen:
            continue

        if stochastic:
            # For stochastic: collect distribution across seeds
            distribution = []
            for seed in range(STOCHASTIC_SEEDS):
                ok, result, _ = _run_fn(fn_source, context, fn_name, args, {}, seed=seed)
                if ok:
                    distribution.append(result)
            if len(distribution) < STOCHASTIC_SEEDS // 2:
                continue
            seen.add(key)
            tests.append({'args': args, 'kwargs': {}, 'distribution': distribution,
                          'stochastic': True})
        else:
            ok, result, err = _run_fn(fn_source, context, fn_name, args, {})
            if not ok:
                continue
            seen.add(key)
            tests.append({'args': args, 'kwargs': {}, 'expected': result, 'stochastic': False})

    return tests


# ── scanning the repo ─────────────────────────────────────────────────────────

def _should_skip_file(path: Path) -> bool:
    parts = set(path.parts)
    skip_dirs = {'__pycache__', '.git', 'node_modules', 'venv', '.venv',
                 'dist', 'build', 'codeeval', 'tests', 'test',
                 'site-packages', 'migrations', 'generated', 'target'}
    return bool(parts & skip_dirs) or path.name.startswith('_')


def _harvest_file(py_file: Path, orbit_dir: Path, rng: random.Random) -> List[Dict]:
    """Harvest tasks from one file; returns [] on any error."""
    try:
        source = py_file.read_text(encoding='utf-8', errors='replace')
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError:
        return []

    module_imports = _collect_imports(tree)
    if module_imports & UNSAFE_IMPORTS:
        return []

    rel_path = str(py_file.relative_to(orbit_dir))
    found = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        fn = node
        # Skip private, dunders, trivial, async, methods (need self)
        if fn.name.startswith('_'):
            continue
        if _is_method(fn):
            continue
        if _arg_count(fn) == 0:
            continue
        if fn.end_lineno - fn.lineno < 3:
            continue
        if _is_impure(fn):
            continue

        stochastic = _is_stochastic(fn)
        fn_source = _get_fn_source(source, fn)
        context = _context_for(py_file, fn, tree, source)
        stub = _make_stub(fn, source)

        tests = _make_tests(fn, fn_source, context, stochastic, rng)
        if len(tests) < 2:
            continue
        # Skip tasks where every test expected None (likely error paths only)
        non_none = [t for t in tests if not t.get('stochastic') and t.get('expected') is not None]
        if not stochastic and len(non_none) < 2:
            continue

        found.append({
            'name': fn.name,
            'file': rel_path,
            'context': context,
            'stub': stub,
            'original': fn_source,
            'tests': tests,
            'stochastic': stochastic,
            'lineno': fn.lineno,
        })
    return found


def harvest(orbit_dir: Path, n: int = MAX_TASKS, seed: int = 42,
            timeout_per_file: float = 5.0) -> List[Dict]:
    import signal

    rng = random.Random(seed)
    candidates = []

    def _alarm(signum, frame):
        raise TimeoutError()

    py_files = sorted(orbit_dir.rglob('*.py'))
    for py_file in py_files:
        if _should_skip_file(py_file):
            continue
        # Per-file timeout: skip files that take too long (e.g., generated code)
        old = signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(int(timeout_per_file))
        try:
            found = _harvest_file(py_file, orbit_dir, rng)
            candidates.extend(found)
        except TimeoutError:
            pass
        except Exception:
            pass
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)

    rng.shuffle(candidates)
    tasks = candidates[:n]
    print(f'harvested {len(tasks)} tasks from {len(candidates)} candidates in {orbit_dir}',
          file=sys.stderr)
    return tasks


# ── generating the game class source ─────────────────────────────────────────

GAME_TEMPLATE = '''"""CodeRecon — reconstruct ablated functions from the orbit mod repo.

A coding reconstruction benchmark. Each round: one function from the orbit
codebase is ablated (body replaced with `pass`). Players reconstruct the body.
The arena executes both original and reconstruction against pre-computed test
vectors and scores by pass rate.

Deterministic tasks: exact match required for each test vector.
Stochastic tasks: scored by distribution overlap (Jaccard similarity) across
{stochastic_seeds} seeded runs.

Upload and play:
    m arena/upload path=coderecon.py
    m arena/enter name=claude kind=model config='{{"model":"claude-sonnet-4-6","task":"code"}}'
    m arena/play game=coderecon players=claude,gpt4
"""

import sys

# ── task bank (generated by src/codeeval/harvest.py) ─────────────────────────
TASKS = {tasks_json}

STOCHASTIC_SEEDS = {stochastic_seeds}


def _score_deterministic(tests, fn, ns):
    passed = 0
    for t in tests:
        try:
            got = fn(*t['args'], **t.get('kwargs', {{}}))
            if got == t['expected']:
                passed += 1
        except Exception:
            pass
    return passed / len(tests) if tests else 0.0


def _score_stochastic(tests, fn, ns, context_src):
    """Score by Jaccard overlap of output distributions."""
    import random as _r
    total_overlap = 0.0
    for t in tests:
        ref_dist = set(map(str, t.get('distribution', [])))
        got_dist = set()
        for seed in range(STOCHASTIC_SEEDS):
            _r.seed(seed)
            try:
                out = fn(*t['args'], **t.get('kwargs', {{}}))
                got_dist.add(str(out))
            except Exception:
                pass
        if not ref_dist and not got_dist:
            total_overlap += 1.0
        elif not ref_dist or not got_dist:
            total_overlap += 0.0
        else:
            overlap = len(ref_dist & got_dist) / len(ref_dist | got_dist)
            total_overlap += overlap
    return total_overlap / len(tests) if tests else 0.0


class CodeRecon:
    """Reconstruct ablated functions. Score = test pass rate."""

    name = 'coderecon'
    players = 1
    max_turns = 1

    def __init__(self, seed):
        self.seed = seed
        self.task = TASKS[seed % len(TASKS)]
        self.submission = None
        self.score = None
        self.detail = {{}}

    def view(self, seat):
        t = self.task
        tests_shown = t['tests'][:3]
        test_lines = []
        for i, tc in enumerate(tests_shown):
            if tc.get('stochastic'):
                dist = tc.get('distribution', [])
                test_lines.append(
                    f"  [{{i}}] args={{tc['args']!r}} → distribution sample: {{dist[:4]!r}}"
                )
            else:
                test_lines.append(
                    f"  [{{i}}] args={{tc['args']!r}} → {{tc['expected']!r}}"
                )
        test_block = '\\n'.join(test_lines)
        stoch_note = ' (stochastic — scored by distribution overlap)' if t['stochastic'] else ''
        context_block = t['context'].strip()
        return (
            f"TASK: Reconstruct `{{t['name']}}` from {{t['file']}}{{stoch_note}}\\n"
            f"\\nCONTEXT (already in scope):\\n{{context_block}}\\n"
            f"\\nSTUB:\\n{{t['stub']}}\\n"
            f"\\nSAMPLE TESTS ({{len(t['tests'])}} total, 3 shown):\\n{{test_block}}\\n"
            f"\\nReply with ONLY the complete function definition — no prose, no fences."
        )

    def step(self, moves):
        code = str(moves.get(0, '')).strip()
        # Strip markdown fences if a model wraps the answer
        if code.startswith('```'):
            lines = code.splitlines()
            code = '\\n'.join(
                l for l in lines if not l.startswith('```')
            ).strip()
        if not code:
            self.score = 0.0
            return {{0: False, 'note': 'empty submission'}}

        self.submission = code
        t = self.task
        ns = {{}}
        try:
            exec(t['context'], ns)
            exec(code, ns)
        except Exception as e:
            self.score = 0.0
            self.detail = {{'exec_error': str(e)}}
            return {{0: False, 'note': f'exec failed: {{e}}'}}

        fn = ns.get(t['name'])
        if fn is None:
            self.score = 0.0
            self.detail = {{'missing': t['name']}}
            return {{0: False, 'note': f"`{{t['name']}}` not defined in submission"}}

        try:
            if t['stochastic']:
                self.score = _score_stochastic(t['tests'], fn, ns, t['context'])
            else:
                self.score = _score_deterministic(t['tests'], fn, ns)
        except Exception as e:
            self.score = 0.0
            self.detail = {{'score_error': str(e)}}

        self.detail['passed'] = int(self.score * len(t['tests']))
        self.detail['total'] = len(t['tests'])
        return {{0: self.score >= 0.5}}

    def done(self):
        return self.score is not None

    def result(self):
        t = self.task
        passed = self.detail.get('passed', 0)
        total = self.detail.get('total', len(t['tests']))
        return {{
            'scores': [round(self.score or 0.0, 4)],
            'summary': (
                f"{{t['name']}} ({{t['file']}}): "
                f"{{passed}}/{{total}} tests — score {{self.score:.2f}}"
            ),
        }}
'''


def generate_game_source(tasks: List[Dict], stochastic_seeds: int = STOCHASTIC_SEEDS) -> str:
    tasks_json = json.dumps(tasks, indent=2)
    return GAME_TEMPLATE.format(
        tasks_json=tasks_json,
        stochastic_seeds=stochastic_seeds,
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--orbit', default=str(ORBIT_DIR), help='path to orbit directory')
    p.add_argument('--out', default='tasks.json', help='output JSON path')
    p.add_argument('--generate', metavar='PATH', help='also write coderecon.py here')
    p.add_argument('--n', type=int, default=MAX_TASKS, help='max tasks')
    p.add_argument('--seed', type=int, default=42, help='RNG seed')
    args = p.parse_args()

    tasks = harvest(Path(args.orbit), n=args.n, seed=args.seed)
    with open(args.out, 'w') as f:
        json.dump(tasks, f, indent=2)
    print(f'wrote {len(tasks)} tasks to {args.out}')

    if args.generate:
        src = generate_game_source(tasks)
        with open(args.generate, 'w') as f:
            f.write(src)
        print(f'wrote game class to {args.generate}')
