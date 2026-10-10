"""harvest.py — turn a repo of choice into a coding game the arena can play.

Point it at a repository (a path on this box or a git URL) and it walks the
Python in it, finds functions that are pure enough to grade, runs each one in
the arena's own class sandbox to record what it answers, and writes a game
class whose rounds are *that repo's* functions with their bodies removed.

The tests are not written by anyone. They are what the real function did.

    python harvest.py --repo https://github.com/psf/requests --out tasks.json
    python harvest.py --repo /root/mod/mod/orbit/hyperliquid --game hl.py

or, from the fleet, in one call that also uploads it:

    m arena/codegame repo=https://github.com/psf/requests

The generated game asks its seats for code (`answer = "code"`), seats one to
eight of them at once, and scores a round by the fraction of the held-out
vectors a submission reproduces. Grading runs through `judge()` — the sandbox
door that runs a player's source without handing a class `exec` — and so does
the harvest itself, which is the point: a reference implementation that cannot
run in the cage never becomes a task, so no task is unwinnable by construction.
"""

import ast
import json
import os
import pprint
import random
import re
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── config ────────────────────────────────────────────────────────────────────

ARENA_SRC = Path(__file__).resolve().parents[1]          # arena/src
ORBIT_DIR = Path(__file__).resolve().parents[3]          # orbit/
REPO_CACHE = Path(os.environ.get('ARENA_STATE', str(Path.home() / '.mod' / 'arena'))) / 'repos'
MAX_TASKS = 60
MAX_TESTS_PER_TASK = 12
STOCHASTIC_SEEDS = 8   # seeds to sample for probabilistic functions
MAX_CONTEXT_LINES = 40  # max lines of context to include per task
MAX_VALUE_CHARS = 1200  # a vector whose answer is a wall of text is not a test
MAX_TASK_CHARS = 24000  # …and a task that big does not belong in a view either

# Names that, used *inside a function*, mean it is not a pure computation and
# cannot be graded by what it returns. The module's own imports are not the
# test: a file that imports `requests` at the top is still allowed to hold a
# pure helper, and the helper is what we are after. Nothing is imported from
# the file either way — the harvest runs the function alone, with only safe
# stdlib in scope, so a function that reaches for any of this simply produces
# no vectors and is dropped for that.
UNSAFE_NAMES = {
    'requests', 'urllib', 'httpx', 'socket', 'subprocess', 'multiprocessing',
    'threading', 'asyncio', 'aiohttp', 'flask', 'fastapi', 'django',
    'sqlalchemy', 'psycopg2', 'pymongo', 'redis', 'boto3', 'azure',
    'torch', 'tensorflow', 'keras', 'cv2', 'np', 'numpy', 'pd', 'pandas', 'scipy',
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


# ── guessing what a function takes ───────────────────────────────────────────
#
# Names and annotations are the only evidence there is, and they run out fast:
# `nums` is a list and `n` is a number, but a substring test calls them both a
# number. So a guess is a *kind per argument*, and when the first guess does
# not run, the harvest tries other shapes for the same function rather than
# dropping it. What proves a guess is the function running, never the name.

KINDS = ('int', 'float', 'str', 'list', 'dict', 'bool')

_INT_NAMES = {'n', 'num', 'number', 'count', 'size', 'length', 'len', 'k', 'i', 'j',
              'idx', 'index', 'width', 'height', 'depth', 'limit', 'base', 'digits',
              'steps', 'bits', 'year', 'age', 'total', 'amount', 'position', 'pos'}
_FLOAT_NAMES = {'x', 'y', 'z', 'val', 'value', 'rate', 'prob', 'weight', 'ratio',
                'angle', 'radius', 'temp', 'price', 'score', 'factor'}
_STR_NAMES = {'s', 'text', 'string', 'name', 'key', 'word', 'prefix', 'suffix',
              'sep', 'delim', 'delimiter', 'char', 'letter', 'line', 'message',
              'msg', 'path', 'sentence', 'phrase', 'pattern'}
_LIST_NAMES = {'lst', 'list', 'arr', 'array', 'items', 'data', 'seq', 'sequence',
               'nums', 'numbers', 'vals', 'values', 'elements', 'collection',
               'points', 'words', 'rows', 'entries', 'sequences', 'nodes'}
_DICT_NAMES = {'d', 'dct', 'mapping', 'config', 'params', 'opts', 'options',
               'graph', 'table', 'counts', 'lookup'}
_BOOL_NAMES = {'flag', 'enabled', 'active', 'reverse', 'ascending', 'descending',
               'verbose', 'strict', 'sort', 'debug'}


def _annotation_kind(ann) -> str:
    """The kind an annotation names, if it names one this can generate."""
    if ann is None:
        return ''
    text = ''
    if isinstance(ann, ast.Name):
        text = ann.id.lower()
    elif isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        text = ann.value.lower()
    elif isinstance(ann, ast.Subscript) and isinstance(ann.value, ast.Name):
        text = ann.value.id.lower()
    elif isinstance(ann, ast.Attribute):
        text = ann.attr.lower()
    return {
        'int': 'int', 'integer': 'int',
        'float': 'float', 'complex': '',
        'str': 'str', 'string': 'str',
        'list': 'list', 'sequence': 'list', 'iterable': 'list', 'tuple': 'list',
        'dict': 'dict', 'mapping': 'dict',
        'bool': 'bool',
    }.get(text, '')


def _name_kind(name: str) -> str:
    """The kind a parameter name suggests. Whole words, then word parts."""
    low = name.lower().strip('_')
    for names, kind in ((_LIST_NAMES, 'list'), (_DICT_NAMES, 'dict'),
                        (_BOOL_NAMES, 'bool'), (_STR_NAMES, 'str'),
                        (_FLOAT_NAMES, 'float'), (_INT_NAMES, 'int')):
        if low in names:
            return kind
    parts = set(re.split(r'[^a-z0-9]+', low)) | {low}
    for names, kind in ((_LIST_NAMES, 'list'), (_DICT_NAMES, 'dict'),
                        (_BOOL_NAMES, 'bool'), (_STR_NAMES, 'str'),
                        (_FLOAT_NAMES, 'float'), (_INT_NAMES, 'int')):
        if parts & names:
            return kind
    if low.endswith('s') and len(low) > 3:      # a plural is usually several things
        return 'list'
    return ''


def _value(kind: str, rng: random.Random) -> Any:
    if kind == 'int':
        return rng.randint(0, 24)
    if kind == 'float':
        return round(rng.uniform(0.0, 10.0), 2)
    if kind == 'str':
        return rng.choice(['hello', 'world', 'foo', 'bar', 'baz', 'abc', 'xyz',
                           'the quick brown fox', 'a', '123', 'Hello World'])
    if kind == 'list':
        return [rng.randint(0, 100) for _ in range(rng.randint(2, 6))]
    if kind == 'dict':
        return {f'k{i}': rng.randint(0, 10) for i in range(rng.randint(1, 3))}
    if kind == 'bool':
        return rng.choice([True, False])
    return rng.randint(0, 10)


def _params(fn: ast.FunctionDef) -> List[ast.arg]:
    args = list(fn.args.args)
    if args and args[0].arg in ('self', 'cls'):
        args = args[1:]
    return args


def _profiles(fn: ast.FunctionDef) -> List[List[str]]:
    """Kinds to try for this function's parameters, best guess first.

    An annotated parameter is never second-guessed. An unannotated one falls
    back through int → str → list, which is most of what a small pure function
    takes, and costs one batch of calls each to find out.
    """
    params = _params(fn)
    first, fixed = [], []
    for arg in params:
        ann = _annotation_kind(arg.annotation)
        fixed.append(bool(ann))
        first.append(ann or _name_kind(arg.arg) or 'int')

    out = [first]
    for fallback in ('int', 'str', 'list', 'float'):
        alt = [k if fix else fallback for k, fix in zip(first, fixed)]
        if alt not in out:
            out.append(alt)
    return out


def _gen_inputs(fn: ast.FunctionDef, rng: random.Random,
                profile: Optional[List[str]] = None) -> List[Any]:
    """One argument tuple for this function, of the kinds in `profile`."""
    kinds = profile or _profiles(fn)[0]
    return [_value(kind, rng) for kind in kinds]


_HOST = None


def _host():
    """The arena's class sandbox, imported once.

    Harvesting runs the repo's own code to find out what it answers, and that
    is arbitrary code off the internet. It runs where a player's submission
    will run: restricted builtins, an import allowlist, a seeded `random`, a
    per-call deadline, and no `open`. A function the cage will not run is a
    function that cannot be a fair task anyway.
    """
    global _HOST
    if _HOST is None:
        sys.path.insert(0, str(ARENA_SRC / 'runtime'))
        import host as _h
        _HOST = _h
    return _HOST


def _run_calls(fn_source: str, context: str, fn_name: str,
               calls: List[Dict], seed: Optional[int] = None,
               timeout: float = 2.0) -> List[Dict]:
    """Run the reference function over a batch of calls. One result per call."""
    out = _host().judge(context + '\n' + fn_source, fn_name, calls,
                        seed=seed, timeout=max(1, int(timeout)))
    if not out.get('ok'):
        return [{'ok': False, 'value': None, 'error': out.get('error', '')}
                for _ in calls]
    return out.get('results', [])


def _jsonable(value: Any) -> bool:
    """Serializable *and* small enough to show. A function that answers with a
    100k-element list is a fine function and a useless exam question: nobody
    can read the vector, and the game file would be measured in megabytes."""
    try:
        return len(json.dumps(value)) <= MAX_VALUE_CHARS
    except (TypeError, ValueError):
        return False


def _run_fn(fn_source: str, context: str, fn_name: str,
            args: List[Any], kwargs: Dict[str, Any],
            seed: Optional[int] = None) -> Tuple[bool, Any, str]:
    """One call. Kept for callers that want the single-shot shape."""
    got = _run_calls(fn_source, context, fn_name,
                     [{'args': args, 'kwargs': kwargs}], seed=seed)
    if not got:
        return False, None, 'no result'
    r = got[0]
    if not r.get('ok'):
        return False, None, r.get('error', '')
    if not _jsonable(r.get('value')):
        return False, None, 'result not serializable'
    return True, r.get('value'), ''


def _vectors(fn, fn_source, context, fn_name, profile, stochastic, rng):
    """Vectors for one profile of argument kinds. Empty if it does not run."""
    pool, seen = [], set()
    attempts = 0
    while len(pool) < MAX_TESTS_PER_TASK * 3 and attempts < MAX_TESTS_PER_TASK * 8:
        attempts += 1
        args = _gen_inputs(fn, rng, profile)
        key = json.dumps(args, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        pool.append(args)
    if not pool:
        return []

    tests = []
    if stochastic:
        for args in pool[:MAX_TESTS_PER_TASK]:
            calls = [{'args': args, 'kwargs': {}, 'seed': s} for s in range(STOCHASTIC_SEEDS)]
            got = _run_calls(fn_source, context, fn_name, calls)
            dist = [r['value'] for r in got if r.get('ok') and _jsonable(r.get('value'))]
            if len(dist) < STOCHASTIC_SEEDS // 2:
                continue
            tests.append({'args': args, 'kwargs': {}, 'distribution': dist,
                          'stochastic': True})
        return tests

    got = _run_calls(fn_source, context, fn_name,
                     [{'args': a, 'kwargs': {}} for a in pool])
    for args, r in zip(pool, got):
        if len(tests) >= MAX_TESTS_PER_TASK:
            break
        if not r.get('ok') or not _jsonable(r.get('value')):
            continue
        tests.append({'args': args, 'kwargs': {}, 'expected': r['value'],
                      'stochastic': False})
    return tests


def _make_tests(fn: ast.FunctionDef, fn_source: str, context: str,
                stochastic: bool, rng: random.Random) -> List[Dict]:
    """Generate test vectors by running the original function in the cage.

    The arguments are guessed, so the first guess is often the wrong shape —
    `nums` looks like a number to anything reading names. Each guess is tried
    in turn and the one the function actually answers wins; a function that
    answers nothing at all is not a task.
    """
    best = []
    for profile in _profiles(fn):
        tests = _vectors(fn, fn_source, context, fn.name, profile, stochastic, rng)
        if len(tests) > len(best):
            best = tests
        if len(best) >= max(4, MAX_TESTS_PER_TASK // 2):
            break
    return best


# ── scanning the repo ─────────────────────────────────────────────────────────

def _should_skip_file(path: Path) -> bool:
    parts = set(path.parts)
    skip_dirs = {'__pycache__', '.git', 'node_modules', 'venv', '.venv',
                 'dist', 'build', 'codeeval', 'tests', 'test',
                 'site-packages', 'migrations', 'generated', 'target'}
    return bool(parts & skip_dirs) or path.name.startswith('_')


def _harvest_file(py_file: Path, repo_dir: Path, rng: random.Random,
                  found: Optional[List[Dict]] = None) -> List[Dict]:
    """Harvest tasks from one file; returns [] on any error.

    `found` is appended to as each task is finished, so a caller that gives up
    on a slow file — running a repository's own code is slow, and some files
    are pathological — keeps whatever the file had already yielded rather than
    throwing the whole file away.
    """
    try:
        source = py_file.read_text(encoding='utf-8', errors='replace')
        tree = ast.parse(source, filename=str(py_file))
    except (SyntaxError, ValueError, OSError):
        return []

    try:
        rel_path = str(py_file.relative_to(repo_dir))
    except ValueError:
        rel_path = py_file.name
    found = [] if found is None else found

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
        if _is_impure(fn) or _body_uses(fn, UNSAFE_NAMES):
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
        # …and tasks where every vector gives the same answer: a constant is
        # reconstructed by returning it, which measures nothing.
        if not stochastic:
            answers = {json.dumps(t['expected'], sort_keys=True, default=str) for t in tests}
            if len(answers) < 2:
                continue

        doc = ast.get_docstring(fn) or ''
        task = {
            'name': fn.name,
            'file': rel_path,
            'context': context,
            'stub': stub,
            'original': fn_source,
            'doc': doc.strip().split('\n')[0][:200],
            'tests': tests,
            'stochastic': stochastic,
            'lineno': fn.lineno,
            'lines': fn.end_lineno - fn.lineno + 1,
        }
        if len(json.dumps(task, default=str)) > MAX_TASK_CHARS:
            continue
        found.append(task)
    return found


# ── the repo of choice ───────────────────────────────────────────────────────

def slugify(text: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', str(text).lower()).strip('-')
    return slug[:40] or 'repo'


def resolve_repo(repo: str, refresh: bool = False) -> Dict[str, Any]:
    """A repo of choice, as a directory on this box.

    `repo` is a path, a git URL, or a GitHub `owner/name`. A URL is cloned
    shallow into the arena's repo cache and reused after that; `refresh` pulls
    it again. Returns {path, name, slug, url, commit, source}.
    """
    import subprocess

    text = str(repo or '').strip()
    if not text:
        raise ValueError('name a repo: a path, a git URL, or owner/name')

    local = Path(text).expanduser()
    if local.exists():
        path = local.resolve()
        url = ''
        try:
            url = subprocess.run(['git', '-C', str(path), 'remote', 'get-url', 'origin'],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            pass
        return {'path': str(path), 'name': path.name, 'slug': slugify(path.name),
                'url': url, 'commit': _commit(path), 'source': 'local'}

    url = text
    if re.fullmatch(r'[\w.-]+/[\w.-]+', text):
        url = f'https://github.com/{text}'
    if not re.match(r'^(https?|git|ssh)://|^git@', url):
        raise ValueError(f'{text!r} is neither a path on this box nor a repo URL')

    name = url.rstrip('/').split('/')[-1]
    if name.endswith('.git'):
        name = name[:-4]
    slug = slugify(name)
    REPO_CACHE.mkdir(parents=True, exist_ok=True)
    path = REPO_CACHE / slug

    if path.exists() and not refresh:
        return {'path': str(path), 'name': name, 'slug': slug, 'url': url,
                'commit': _commit(path), 'source': 'cache'}
    if path.exists():
        subprocess.run(['git', '-C', str(path), 'fetch', '--depth', '1', 'origin'],
                       capture_output=True, text=True, timeout=600)
        subprocess.run(['git', '-C', str(path), 'reset', '--hard', 'origin/HEAD'],
                       capture_output=True, text=True, timeout=120)
    else:
        done = subprocess.run(['git', 'clone', '--depth', '1', url, str(path)],
                              capture_output=True, text=True, timeout=900)
        if done.returncode != 0:
            raise RuntimeError(f'clone of {url} failed: {done.stderr.strip()[:400]}')
    return {'path': str(path), 'name': name, 'slug': slug, 'url': url,
            'commit': _commit(path), 'source': 'clone'}


def _commit(path: Path) -> str:
    import subprocess
    try:
        out = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip()[:12] if out.returncode == 0 else ''
    except Exception:
        return ''


def harvest(repo_dir, n: int = MAX_TASKS, seed: int = 42,
            timeout_per_file: float = 25.0, quiet: bool = False) -> List[Dict]:
    """Every gradeable function in a repo, shuffled, capped at `n`."""
    import signal

    repo_dir = Path(repo_dir)
    rng = random.Random(seed)
    candidates = []

    def _alarm(signum, frame):
        raise TimeoutError()

    # Shuffled, then scanned only until there is enough: a repository with ten
    # thousand Python files does not need running end to end to fill a bank of
    # forty tasks, and reading it in seed order keeps the variety a sorted walk
    # would lose (every task from `a/` and none from `z/`).
    py_files = sorted(repo_dir.rglob('*.py'))
    rng.shuffle(py_files)
    enough = max(int(n) * 3, 30)
    for py_file in py_files:
        if len(candidates) >= enough:
            break
        if _should_skip_file(py_file):
            continue
        try:
            if py_file.stat().st_size > 400_000:
                continue
        except OSError:
            continue
        # Per-file timeout: skip files that take too long (e.g., generated code)
        old = signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(int(timeout_per_file))
        try:
            _harvest_file(py_file, repo_dir, rng, candidates)
        except TimeoutError:
            pass
        except Exception:
            pass
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)

    # Prefer meatier functions, then shuffle within that so one file cannot
    # own the whole game.
    rng.shuffle(candidates)
    candidates.sort(key=lambda t: -min(t.get('lines', 0), 30))
    tasks = candidates[:n]
    if not quiet:
        print(f'harvested {len(tasks)} tasks from {len(candidates)} candidates '
              f'in {repo_dir}', file=sys.stderr)
    return tasks


# ── generating the game class source ─────────────────────────────────────────

GAME_TEMPLATE = '''"""{title} — reconstruct functions from {repo_name}.

A coding game whose rounds are real functions out of {repo_label}, with their
bodies removed. Each round every seat is shown the signature, the docstring,
the surrounding context and three example calls, and answers with a whole
function. The arena runs it against the vectors the original function itself
answered — most of them held back — and the round score is the fraction it
reproduces.

  repo     {repo_url}
  commit   {repo_commit}
  tasks    {task_count} functions from {file_count} files
  rounds   {rounds} per match, drawn by the match seed
  scoring  fraction of held-out vectors reproduced; stochastic functions are
           scored by how far their spread of answers overlaps the original's

Nobody wrote these tests. They are what the repo already does.
"""

ROUNDS = {rounds}
SHOWN = 3                     # example calls a seat is shown; the rest are held back
STOCHASTIC_SEEDS = {stochastic_seeds}
REPO = {repo_json}

# ── the task bank (generated by arena/src/codeeval/harvest.py) ───────────────
TASKS = {tasks_json}


try:                          # `judge` is the sandbox door the arena hands a game:
    judge                     # running a submission is the one thing a class cannot
except NameError:             # do for itself, since `exec` is denied in the cage.
    def judge(code, name="", calls=None, context="", seed=None, timeout=5):
        ns = {{}}
        try:
            exec(compile((context or "") + "\\n" + code, "<candidate>", "exec"), ns)
        except Exception as e:
            return {{"ok": False, "error": "{{}}: {{}}".format(type(e).__name__, e), "results": []}}
        got = ns.get(name)
        if not callable(got):
            return {{"ok": False, "error": "`" + str(name) + "` is not defined", "results": []}}
        import random as _r
        out = []
        for call in (calls or []):
            if call.get("seed") is not None:
                _r.seed(call["seed"])
            try:
                out.append({{"ok": True, "error": "",
                            "value": got(*call.get("args", []), **call.get("kwargs", {{}}))}})
            except Exception as e:
                out.append({{"ok": False, "value": None,
                            "error": "{{}}: {{}}".format(type(e).__name__, e)}})
        return {{"ok": True, "error": "", "results": out}}


def same(a, b):
    """Equal enough. Floats compare to a tolerance, everything else exactly."""
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(b)))
        except (TypeError, ValueError):
            return False
    return a == b


def score_exact(task, code):
    """Fraction of the graded vectors a submission reproduces."""
    tests = task['tests']
    calls = [{{'args': t['args'], 'kwargs': t.get('kwargs', {{}})}} for t in tests]
    out = judge(code, task['name'], calls, context=task['context'], timeout=3)
    if not out.get('ok'):
        return 0.0, 0, out.get('error', 'the submission did not load')
    passed, why = 0, ''
    for t, r in zip(tests, out.get('results', [])):
        if r.get('ok') and same(r.get('value'), t['expected']):
            passed += 1
        elif not why:
            why = r.get('error') or '{{}}({{}}) gave {{!r}}, wanted {{!r}}'.format(
                task['name'], ', '.join(repr(a) for a in t['args']),
                r.get('value'), t['expected'])
    return (passed / len(tests) if tests else 0.0), passed, why


def score_spread(task, code):
    """Overlap of output spreads, for a function that uses randomness."""
    tests = task['tests']
    total, why = 0.0, ''
    for t in tests:
        calls = [{{'args': t['args'], 'kwargs': t.get('kwargs', {{}}), 'seed': s}}
                 for s in range(STOCHASTIC_SEEDS)]
        out = judge(code, task['name'], calls, context=task['context'], timeout=3)
        if not out.get('ok'):
            return 0.0, 0, out.get('error', 'the submission did not load')
        mine = set(str(r.get('value')) for r in out.get('results', []) if r.get('ok'))
        theirs = set(str(v) for v in t.get('distribution', []))
        if not mine and not why:
            why = next((r.get('error', '') for r in out.get('results', [])
                        if not r.get('ok')), '')
        if mine and theirs:
            total += len(mine & theirs) / len(mine | theirs)
    n = len(tests) or 1
    return total / n, int(round(total)), why


class {class_name}:
    """{one_liner}"""

    name = '{game_name}'
    players = (1, 8)          # solo practice, or up to eight seats sitting the same paper
    max_turns = ROUNDS
    answer = 'code'           # every seat is asked for a whole function

    def __init__(self, seed):
        self.seed = seed
        self.round = 0
        # The seed picks the rounds, so two players seeded alike sat the same exam.
        pick = random.Random(seed)
        order = list(range(len(TASKS)))
        pick.shuffle(order)
        self.order = order[:ROUNDS] or [0]
        self.scores = {{}}
        self.card = []
        self.last = {{}}
        self.over = False

    # Every seat answers at once: a seat that saw another's code would be
    # copying, not writing. Sixteen is more seats than the arena allows — the
    # host keeps the ones that exist and drops the rest.
    def turn(self):
        return list(range(16))

    def task(self):
        return TASKS[self.order[min(self.round, len(self.order) - 1)]]

    def view(self, seat):
        t = self.task()
        lines = [
            '{{}} — round {{}} of {{}}.'.format(REPO.get('name', 'repo'),
                                                self.round + 1, len(self.order)),
            'Reconstruct `{{}}` from {{}}:{{}}.'.format(t['name'], t['file'], t['lineno']),
        ]
        if t.get('doc'):
            lines.append('What it is for: ' + t['doc'])
        if t['stochastic']:
            lines.append('It uses randomness: you are scored on the spread of answers '
                         'it gives, not on any one of them.')
        if t['context'].strip():
            lines += ['', 'ALREADY IN SCOPE:', t['context'].strip()]
        lines += ['', 'WRITE THIS:', t['stub'], '',
                  'WORKED CALLS ({{}} are graded, {{}} shown):'.format(len(t['tests']), SHOWN)]
        for tc in t['tests'][:SHOWN]:
            call = '{{}}({{}})'.format(t['name'], ', '.join(repr(a) for a in tc['args']))
            if tc.get('stochastic'):
                sample = [str(v) for v in tc.get('distribution', [])[:4]]
                lines.append('  {{}} -> one of {{}}'.format(call, sample))
            else:
                lines.append('  {{}} -> {{!r}}'.format(call, tc['expected']))
        if self.last.get(seat):
            lines += ['', 'Last round: ' + self.last[seat]]
        lines += ['', 'Write the whole `def {{}}(...)`. What is in scope above is in '
                      'scope for you, and nothing else is.'.format(t['name'])]
        return "\\n".join(lines)

    def step(self, moves):
        t = self.task()
        legal, notes = {{}}, []
        # The host hands every move over twice — keyed 0 and keyed "0", so that
        # both `moves[0]` and `moves["0"]` work. Score each seat once.
        for raw_seat, raw in sorted(moves.items(), key=lambda kv: str(kv[0])):
            seat = int(raw_seat)
            if seat in legal:
                continue
            code = str(raw or '').strip()
            if code.startswith('```'):
                code = "\\n".join(l for l in code.splitlines()
                                  if not l.strip().startswith('```')).strip()
            if not code or ('def ' + t['name']) not in code:
                legal[seat] = False
                self.scores[seat] = self.scores.get(seat, 0.0)
                self.last[seat] = 'no `def {{}}` in the answer — 0.00'.format(t['name'])
                notes.append('seat {{}}: nothing to run'.format(seat))
                continue
            if t['stochastic']:
                score, passed, why = score_spread(t, code)
            else:
                score, passed, why = score_exact(t, code)
            legal[seat] = score > 0.0
            self.scores[seat] = self.scores.get(seat, 0.0) + score
            self.last[seat] = '{{}} — {{}}/{{}} vectors, {{:.2f}}{{}}'.format(
                t['name'], passed, len(t['tests']), score,
                ' (' + str(why)[:120] + ')' if why else '')
            notes.append('seat {{}}: {{:.2f}}'.format(seat, score))
            self.card.append({{'round': self.round + 1, 'seat': seat,
                              'task': t['name'], 'file': t['file'],
                              'score': round(score, 4)}})
        self.round += 1
        if self.round >= len(self.order):
            self.over = True
        out = dict(legal)
        out['note'] = '{{}} ({{}}) — {{}}'.format(t['name'], t['file'], ', '.join(notes))
        return out

    def done(self):
        return self.over

    def result(self):
        seats = (max(self.scores) + 1) if self.scores else 1
        rounds = len(self.order) or 1
        scores = [round(self.scores.get(i, 0.0) / rounds, 4) for i in range(seats)]
        played = ', '.join(sorted(set(c['task'] for c in self.card))) or 'nothing'
        line = '{{}} function(s) from {{}} ({{}}): '.format(rounds, REPO.get('name', 'repo'), played)
        line += ', '.join('seat {{}} {{:.2f}}'.format(i, s) for i, s in enumerate(scores))
        if seats > 1:
            best = max(range(seats), key=lambda i: scores[i])
            line += ' — seat {{}} takes it'.format(best)
        return {{'scores': scores, 'summary': line, 'card': self.card, 'repo': REPO}}
'''


def one_liner(repo, tasks) -> str:
    return ('Reconstruct {} real functions from {} — graded against what they '
            'actually return.'.format(len(tasks), repo.get('name', 'a repo')))


def generate_game_source(tasks: List[Dict], repo: Dict[str, Any] = None,
                         rounds: int = 3, name: str = '',
                         stochastic_seeds: int = STOCHASTIC_SEEDS) -> str:
    """The game class for a harvest: one file, self-contained, ready to upload."""
    repo = dict(repo or {})
    repo_name = repo.get('name') or 'a repo'
    slug = name or '{}-recon'.format(repo.get('slug') or slugify(repo_name))
    class_name = ''.join(p.capitalize() for p in re.split(r'[^a-zA-Z0-9]+', slug) if p)
    files = sorted(set(t['file'] for t in tasks))
    trimmed = []
    for t in tasks:
        keep = dict(t)
        keep.pop('original', None)        # the answer key never ships with the exam
        trimmed.append(keep)
    return GAME_TEMPLATE.format(
        title=slug,
        class_name=class_name or 'RepoRecon',
        game_name=slug,
        repo_name=repo_name,
        repo_label=repo.get('url') or repo_name,
        repo_url=repo.get('url') or repo.get('path', ''),
        repo_commit=repo.get('commit') or 'unknown',
        # Python literals, not JSON: `false` and `null` are not words here.
        repo_json=repr({k: repo.get(k, '') for k in ('name', 'slug', 'url', 'commit')}),
        one_liner=one_liner(repo, tasks),
        task_count=len(tasks),
        file_count=len(files),
        rounds=max(1, int(rounds)),
        tasks_json=pprint.pformat(trimmed, width=100, sort_dicts=False),
        stochastic_seeds=stochastic_seeds,
    )


def build(repo: str, n: int = MAX_TASKS, rounds: int = 3, seed: int = 42,
          name: str = '', refresh: bool = False, quiet: bool = True) -> Dict[str, Any]:
    """Repo in, game class out. The whole pipeline, for callers that want it."""
    meta = resolve_repo(repo, refresh=refresh)
    tasks = harvest(meta['path'], n=n, seed=seed, quiet=quiet)
    if not tasks:
        raise RuntimeError(
            'no gradeable function found in {} — a repo whose Python is all '
            'methods, all IO or all heavy imports has nothing to ablate'.format(meta['name']))
    source = generate_game_source(tasks, meta, rounds=rounds, name=name)
    return {
        'repo': meta,
        'tasks': tasks,
        'source': source,
        'name': name or '{}-recon'.format(meta['slug']),
        'files': sorted(set(t['file'] for t in tasks)),
        'stochastic': sum(1 for t in tasks if t['stochastic']),
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse

    p = argparse.ArgumentParser(description='turn a repo into an arena coding game')
    p.add_argument('--repo', default=str(ORBIT_DIR),
                   help='a path on this box, a git URL, or owner/name')
    p.add_argument('--out', default='', help='write the task bank here as JSON')
    p.add_argument('--game', metavar='PATH', default='',
                   help='write the game class here (default: <slug>-recon.py)')
    p.add_argument('--name', default='', help='game name (default: <repo>-recon)')
    p.add_argument('--n', type=int, default=MAX_TASKS, help='max tasks to keep')
    p.add_argument('--rounds', type=int, default=3, help='functions per match')
    p.add_argument('--seed', type=int, default=42, help='harvest RNG seed')
    p.add_argument('--refresh', action='store_true', help='re-pull a cached clone')
    p.add_argument('--json', action='store_true',
                   help='print one JSON object on stdout instead of prose')
    args = p.parse_args()

    out = build(args.repo, n=args.n, rounds=args.rounds, seed=args.seed,
                name=args.name, refresh=args.refresh, quiet=False)
    repo = out['repo']
    print(f"{repo['name']} @ {repo['commit'] or 'local'} ({repo['source']}) — "
          f"{len(out['tasks'])} tasks from {len(out['files'])} files, "
          f"{out['stochastic']} stochastic", file=sys.stderr)

    if args.out:
        Path(args.out).write_text(json.dumps(out['tasks'], indent=2))
        print(f"wrote {len(out['tasks'])} tasks to {args.out}")

    game_path = Path(args.game or f"{out['name']}.py")
    game_path.write_text(out['source'])
    if args.json:
        print(json.dumps({'name': out['name'], 'path': str(game_path),
                          'tasks': len(out['tasks']), 'files': out['files'],
                          'stochastic': out['stochastic'], 'repo': repo}))
    else:
        print(f"wrote game `{out['name']}` to {game_path}")
