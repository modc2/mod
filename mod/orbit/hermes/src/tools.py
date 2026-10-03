"""
hermes tools — the small, self-contained loadout a local model can actually
drive.

The module used to reach into `mod.orbit.agent.src.skills` for its tools. That
import resolves to a class with one entry (`pdf`) and no `schema()` method, so
every run built its prompt out of an AttributeError-shaped hole and then had
nothing to call. Depending on another module's private internals for the one
thing this agent has to do was the mistake; these eight are hermes's own.

Eight, not thirty, on purpose. A 3B model reading a wall of tool schemas
spends its context on the menu instead of the task, and picks worse from it.
Everything here is either how you look at a repo or how you change one.

`finish` and `think` are not tools in the sense the others are — nothing runs.
They exist because the loop needs a way for the model to end a run and a way
for it to spend a step reasoning without touching the disk.
"""
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List

# a tool that runs away with the box is worse than a tool that gave up
BASH_TIMEOUT = float(os.environ.get('HERMES_BASH_TIMEOUT', 120))
READ_LIMIT = 60_000          # chars handed back from one file
GREP_LIMIT = 200             # matching lines


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f'\n… [{len(text) - limit} more characters]'


def bash(command: str = '', cwd: str = None, timeout: float = None) -> dict:
    """Run a shell command and hand back what it printed."""
    proc = subprocess.run(command, shell=True, capture_output=True, text=True,
                          cwd=cwd or os.getcwd(),
                          timeout=timeout or BASH_TIMEOUT)
    return {'code': proc.returncode,
            'stdout': _clip(proc.stdout, READ_LIMIT),
            'stderr': _clip(proc.stderr, 4000)}


def read(file_path: str = '', offset: int = 0, limit: int = 0) -> str:
    """Read a file, optionally a line window of it."""
    p = Path(file_path).expanduser()
    text = p.read_text(errors='replace')
    if offset or limit:
        lines = text.splitlines()
        end = offset + limit if limit else len(lines)
        text = '\n'.join(lines[offset:end])
    return _clip(text, READ_LIMIT)


def write(file_path: str = '', content: str = '') -> dict:
    """Write a file whole, creating parent directories."""
    p = Path(file_path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return {'written': str(p), 'bytes': len(content)}


def edit(file_path: str = '', old_string: str = '', new_string: str = '') -> dict:
    """Replace one exact occurrence of a string in a file.

    Refuses on zero matches and on several, because both mean the model was
    working from a guess about the file rather than from having read it — and
    a replace_all that fires on an ambiguous match is how an edit silently
    rewrites the wrong line.
    """
    p = Path(file_path).expanduser()
    text = p.read_text(errors='replace')
    hits = text.count(old_string)
    if hits == 0:
        raise ValueError(f'old_string not found in {p} — read the file first')
    if hits > 1:
        raise ValueError(
            f'old_string appears {hits} times in {p} — include more surrounding '
            f'context so it matches exactly once')
    p.write_text(text.replace(old_string, new_string, 1))
    return {'edited': str(p), 'replaced': 1}


def ls(path: str = '.', depth: int = 1) -> List[str]:
    """List a directory. Noise directories are skipped."""
    skip = {'.git', 'node_modules', '__pycache__', '.next', 'target', 'venv'}
    root = Path(path).expanduser()
    out = []
    for cur, dirs, files in os.walk(root):
        rel = Path(cur).relative_to(root)
        if len(rel.parts) >= depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith('.')]
        for f in sorted(files):
            out.append(str(rel / f) if rel.parts else f)
        if len(out) > 500:
            break
    return out[:500]


def grep(pattern: str = '', path: str = '.', glob: str = '*') -> List[str]:
    """Search files for a regex, `path:line:text` per hit."""
    rx = re.compile(pattern)
    root = Path(path).expanduser()
    targets = [root] if root.is_file() else root.rglob(glob)
    out = []
    for p in targets:
        if not p.is_file() or any(part in {'.git', 'node_modules', '__pycache__'}
                                  for part in p.parts):
            continue
        try:
            for i, line in enumerate(p.read_text(errors='replace').splitlines(), 1):
                if rx.search(line):
                    out.append(f'{p}:{i}:{line.strip()[:200]}')
                    if len(out) >= GREP_LIMIT:
                        return out
        except (OSError, UnicodeDecodeError):
            continue
    return out


def think(thought: str = '') -> dict:
    """Reason about the task without touching anything."""
    return {'thought': thought}


def finish(summary: str = '') -> dict:
    """End the run. `summary` is what the caller reads."""
    return {'summary': summary}


# name -> (callable, one-line description, {param: type})
REGISTRY: Dict[str, tuple] = {
    'bash':   (bash,   'Run a shell command', {'command': 'str', 'cwd': 'str?'}),
    'read':   (read,   'Read a file', {'file_path': 'str', 'offset': 'int?', 'limit': 'int?'}),
    'write':  (write,  'Write a file whole', {'file_path': 'str', 'content': 'str'}),
    'edit':   (edit,   'Replace one exact string in a file',
               {'file_path': 'str', 'old_string': 'str', 'new_string': 'str'}),
    'ls':     (ls,     'List a directory', {'path': 'str', 'depth': 'int?'}),
    'grep':   (grep,   'Search files for a regex', {'pattern': 'str', 'path': 'str?', 'glob': 'str?'}),
    'think':  (think,  'Reason without acting', {'thought': 'str'}),
    'finish': (finish, 'End the run with a summary for the caller', {'summary': 'str'}),
}

# nothing here reaches the network or the fleet, but two of them write, so a
# sandboxed caller gets the reading half only
READ_ONLY = ('read', 'ls', 'grep', 'think', 'finish')


def names(sandbox: bool = False) -> List[str]:
    return list(READ_ONLY) if sandbox else list(REGISTRY)


def schema(sandbox: bool = False) -> Dict[str, dict]:
    return {n: {'description': REGISTRY[n][1], 'params': REGISTRY[n][2]}
            for n in names(sandbox)}


def describe(sandbox: bool = False) -> str:
    """The tool list as the system prompt carries it."""
    lines = []
    for name in names(sandbox):
        fn, desc, params = REGISTRY[name]
        sig = ', '.join(f'{k}: {v}' for k, v in params.items())
        lines.append(f'- {name}({sig}): {desc}')
    return '\n'.join(lines)


def get(name: str, sandbox: bool = False) -> Callable:
    if name not in REGISTRY:
        raise KeyError(f'unknown tool: {name}')
    if sandbox and name not in READ_ONLY:
        raise PermissionError(f'{name} writes — a sandboxed run gets {READ_ONLY}')
    return REGISTRY[name][0]


def run(name: str, sandbox: bool = False, **params) -> Any:
    fn = get(name, sandbox)
    # a small model volunteers keys the tool never had; drop them rather than
    # failing the step on a TypeError it cannot read
    allowed = set(REGISTRY[name][2])
    clean = {k: v for k, v in params.items() if k.rstrip('?') in
             {a.rstrip('?') for a in allowed}}
    return fn(**clean)
