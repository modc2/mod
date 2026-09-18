"""The read half: a public, read-only view of a module's source tree.

An outside agent cannot recommend anything useful about code it has not read,
and build's file API deliberately confines a peer to their own userspace — so
this module publishes its own scan surface instead of borrowing one.

What it will show is ordinary published source: the files a module ships,
minus the things nobody meant to publish. Four rules do the work:

    * a module marked private in ``~/.mod/build/private/<name>.json`` is not
      here at all — not in ``modules()``, not readable by name;
    * dependency and build output (``node_modules``, ``vendor``, ``target``,
      ``.next``, ``__pycache__`` …) is skipped, because it is not the module
      and it would bury the parts that are;
    * secret-shaped files (``.env``, ``*.pem``, ``*.key``, ``secrets*``) are
      never read, and inside anything that is read, a line that looks like a
      credential comes back with its value replaced;
    * everything is bounded — bytes per file, files per listing, hits per
      grep — so one call cannot walk the box.

``brief()`` is the entry point worth knowing: one call that hands an agent the
config, the README opening, the shape of the tree, the entry points, the
TODO/FIXME lines and the recommendations already filed, which is most of what
it needs to say something specific instead of something generic.
"""

import json
import os
import re
import subprocess

HOME = os.path.expanduser('~')
ANCHOR = os.environ.get('MOD_ANCHOR') or os.path.join(HOME, 'mod', 'mod')
# core before orbit: when a name exists in both trees, core is the real one.
TREES = ('core', 'orbit')
PRIVATE_DIR = os.path.join(HOME, '.mod', 'build', 'private')

SKIP_DIRS = {
    '.git', '.hg', 'node_modules', 'vendor', 'target', '__pycache__', '.next',
    '.next-stage', '.next-dev', 'dist', '.venv', 'venv', 'env', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', '.cache', 'coverage', '.turbo', '.parcel-cache',
    'site-packages', '.terraform', 'out',
}
# Read as text, nothing else. A recommendation about a .png is not a thing.
TEXT_EXT = {
    '.py', '.rs', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs', '.json', '.toml',
    '.yaml', '.yml', '.md', '.txt', '.html', '.css', '.scss', '.sh', '.sql',
    '.go', '.rb', '.java', '.kt', '.c', '.h', '.cpp', '.hpp', '.swift', '.php',
    '.lua', '.ex', '.exs', '.sol', '.proto', '.graphql', '.cfg', '.ini', '.env.example',
}
# Never opened, whatever the extension list says.
SECRET_NAMES = re.compile(
    r'(^\.env$|^\.env\.|(^|[._-])secrets?([._-]|$)|\.pem$|\.key$|\.p12$|\.pfx$'
    r'|^id_rsa|^id_ed25519|\.keystore$|(^|[._-])credentials?([._-]|$)'
    r'|^token$|\.token$|(^|[._-])mnemonic)', re.I)
# A line that looks like it is carrying one. The value goes, the line stays —
# an agent should still be able to see that a key is read here.
SECRET_LINE = re.compile(
    r'''(?i)((?:api[_-]?key|secret|private[_-]?key|passwd|password|mnemonic|seed[_-]?phrase|
        access[_-]?token|auth[_-]?token|bearer|client[_-]?secret)\s*[:=]\s*
        ["']?)([A-Za-z0-9_\-/+=.]{12,})''', re.X)
LONG_HEX = re.compile(r'(?<![A-Za-z0-9])(0x)?[A-Fa-f0-9]{48,}(?![A-Za-z0-9])')

MAX_FILE_BYTES = 200_000
MAX_LINES = 600
MAX_TREE = 600
MAX_HITS = 80


class ScanError(LookupError):
    """Asked for something that is not here, or not ours to show."""


# ── where a module lives ─────────────────────────────────────────────

def _valid(name):
    return bool(name) and re.fullmatch(r'[A-Za-z0-9._-]{1,64}', str(name)) is not None


def private_names():
    """Modules whose owner switched privacy on in build. Invisible here."""
    out = set()
    try:
        for f in os.listdir(PRIVATE_DIR):
            if not f.endswith('.json'):
                continue
            try:
                with open(os.path.join(PRIVATE_DIR, f)) as fh:
                    rec = json.load(fh)
            except Exception:
                continue
            if rec.get('enabled'):
                out.add(str(rec.get('module') or f[:-5]).lower())
    except FileNotFoundError:
        pass
    return out


def module_dir(name, allow_private=False):
    """Absolute path of a scannable module, or raise."""
    if not _valid(name):
        raise ScanError(f'bad module name: {name!r}')
    if not allow_private and str(name).lower() in private_names():
        raise ScanError(f'no module named {name!r}')      # private == absent
    for tree in TREES:
        path = os.path.join(ANCHOR, tree, name)
        if os.path.isdir(path):
            return path
    raise ScanError(f'no module named {name!r}')


def config(name):
    """A module's config.json, or {} — every module answers, not all have one."""
    try:
        with open(os.path.join(module_dir(name), 'config.json')) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (ScanError, FileNotFoundError, json.JSONDecodeError, IsADirectoryError):
        return {}


def owner_of(name):
    """Who has to approve a recommendation about this module.

    The module's own `owner` if it declares one, otherwise the address that
    owns this deployment (build's config) — a module with no owner field is
    still somebody's.
    """
    own = str(config(name).get('owner') or '').strip()
    if own:
        return own.lower()
    return deployment_owner()


def deployment_owner():
    try:
        with open(os.path.join(ANCHOR, 'orbit', 'build', 'config.json')) as f:
            return str(json.load(f).get('owner') or '').strip().lower()
    except Exception:
        return ''


# ── walking it ───────────────────────────────────────────────────────

def _skip(entry):
    return entry in SKIP_DIRS or entry.startswith('.next')


def _readable(fname):
    if SECRET_NAMES.search(fname):
        return False
    ext = os.path.splitext(fname)[1].lower()
    return ext in TEXT_EXT or fname in ('Dockerfile', 'Makefile', 'README', 'LICENSE')


def _rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, '/')


def _is_module(path):
    """A directory in the tree is a module if it declares itself one. Keeps
    caches and stray directories out of a listing an agent has to read."""
    return any(os.path.exists(os.path.join(path, marker))
               for marker in ('config.json', 'mod.py', 'src', 'package.json'))


def modules(q=None, limit=400):
    """Every module an outsider may scan, with enough to choose one."""
    hidden = private_names()
    out = []
    for tree in TREES:
        base = os.path.join(ANCHOR, tree)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if not _valid(name) or name.lower() in hidden or _skip(name):
                continue
            path = os.path.join(base, name)
            if not os.path.isdir(path) or not _is_module(path):
                continue
            if any(m['module'] == name for m in out):      # core wins
                continue
            cfg = config(name)
            if q and q.lower() not in f"{name} {cfg.get('description', '')}".lower():
                continue
            out.append({
                'module': name,
                'tree': tree,
                'title': cfg.get('title') or name,
                'description': (cfg.get('description') or '')[:400],
                'version': cfg.get('version'),
                'owner': owner_of(name),
                'port': cfg.get('port'),
            })
            if len(out) >= limit:
                return {'count': len(out), 'modules': out, 'truncated': True}
    return {'count': len(out), 'modules': out, 'truncated': False}


def tree(module, path='', depth=3, limit=MAX_TREE):
    """The module's files — dependency and build directories pruned."""
    root = module_dir(module)
    start = _safe_join(root, path)
    if not os.path.isdir(start):
        raise ScanError(f'{module}/{path or "."} is not a directory')
    depth = max(1, min(int(depth or 3), 8))
    limit = max(1, min(int(limit or MAX_TREE), MAX_TREE))
    files, dirs, truncated = [], [], False
    base_depth = start.rstrip('/').count(os.sep)
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if not _skip(d))
        if dirpath.count(os.sep) - base_depth >= depth:
            dirnames[:] = []
        for d in dirnames:
            dirs.append(_rel(root, os.path.join(dirpath, d)))
        for f in sorted(filenames):
            full = os.path.join(dirpath, f)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            files.append({'path': _rel(root, full), 'bytes': size,
                          'readable': _readable(f)})
            if len(files) >= limit:
                truncated = True
                break
        if truncated:
            break
    return {'module': module, 'path': path or '.', 'depth': depth,
            'dirs': dirs[:limit], 'files': files, 'count': len(files),
            'truncated': truncated}


def _safe_join(root, path):
    """Join inside the module or refuse. `..` is the whole attack surface."""
    path = (path or '').lstrip('/')
    full = os.path.realpath(os.path.join(root, path))
    if full != os.path.realpath(root) and not full.startswith(os.path.realpath(root) + os.sep):
        raise ScanError('path escapes the module')
    return full


def redact(text):
    """Blank out anything shaped like a credential. Cheap, and deliberately
    over-eager: a false positive costs an agent one unreadable literal."""
    text = SECRET_LINE.sub(lambda m: m.group(1) + '«redacted»', text)
    return LONG_HEX.sub('«redacted»', text)


def read(module, path, start=1, lines=MAX_LINES):
    """One file, as text, redacted and bounded."""
    root = module_dir(module)
    full = _safe_join(root, path)
    name = os.path.basename(full)
    if not os.path.isfile(full):
        raise ScanError(f'{module}/{path} not found')
    if not _readable(name):
        raise ScanError(f'{module}/{path} is not published (binary, or secret-shaped)')
    size = os.path.getsize(full)
    with open(full, 'r', encoding='utf-8', errors='replace') as f:
        all_lines = f.read(MAX_FILE_BYTES).splitlines()
    start = max(1, int(start or 1))
    lines = max(1, min(int(lines or MAX_LINES), MAX_LINES))
    chunk = all_lines[start - 1:start - 1 + lines]
    return {
        'module': module, 'path': path, 'bytes': size,
        'total_lines': len(all_lines), 'start': start, 'lines': len(chunk),
        'truncated': size > MAX_FILE_BYTES or start - 1 + len(chunk) < len(all_lines),
        'content': redact('\n'.join(chunk)),
    }


def grep(module, query, glob=None, limit=MAX_HITS):
    """Literal search across the published files. Line numbers included —
    they are what a recommendation anchors to."""
    if not query:
        raise ValueError('grep needs query=')
    root = module_dir(module)
    limit = max(1, min(int(limit or MAX_HITS), MAX_HITS))
    cmd = ['grep', '-rInF', '--', str(query), '.']
    for d in sorted(SKIP_DIRS):
        cmd.insert(4, f'--exclude-dir={d}')
    if glob:
        cmd.insert(4, f'--include={glob}')
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        raise ScanError('search timed out — narrow it with glob=')
    hits = []
    for line in proc.stdout.splitlines():
        parts = line.split(':', 2)
        if len(parts) < 3:
            continue
        rel = parts[0][2:] if parts[0].startswith('./') else parts[0]
        if not _readable(os.path.basename(rel)):
            continue
        hits.append({'path': rel, 'line': int(parts[1]) if parts[1].isdigit() else 0,
                     'text': redact(parts[2])[:300]})
        if len(hits) >= limit:
            break
    return {'module': module, 'query': query, 'count': len(hits),
            'hits': hits, 'truncated': len(hits) >= limit}


# ── the one-call pack ────────────────────────────────────────────────

LANGS = {'.py': 'python', '.rs': 'rust', '.ts': 'typescript', '.tsx': 'typescript',
         '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript',
         '.go': 'go', '.rb': 'ruby', '.sol': 'solidity', '.sh': 'shell',
         '.html': 'html', '.css': 'css', '.md': 'markdown'}
TODO = re.compile(r'\b(TODO|FIXME|HACK|XXX)\b')


def stats(module):
    """Languages, size, and the biggest files — where the module actually is."""
    root = module_dir(module)
    langs, biggest, total, count, todos = {}, [], 0, 0, []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _skip(d)]
        for f in filenames:
            if not _readable(f):
                continue
            full = os.path.join(dirpath, f)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            ext = os.path.splitext(f)[1].lower()
            lang = LANGS.get(ext)
            count += 1
            total += size
            if lang:
                langs[lang] = langs.get(lang, 0) + 1
            biggest.append((size, _rel(root, full)))
            if size < 400_000 and len(todos) < 40:
                try:
                    with open(full, 'r', encoding='utf-8', errors='replace') as fh:
                        for n, line in enumerate(fh, 1):
                            if TODO.search(line):
                                todos.append({'path': _rel(root, full), 'line': n,
                                              'text': redact(line.strip())[:200]})
                                if len(todos) >= 40:
                                    break
                except OSError:
                    pass
    biggest.sort(reverse=True)
    return {
        'module': module, 'files': count, 'bytes': total,
        'languages': dict(sorted(langs.items(), key=lambda kv: -kv[1])),
        'biggest': [{'path': p, 'bytes': s} for s, p in biggest[:12]],
        'todos': todos,
    }


def readme(module, chars=4000):
    for name in ('README.md', 'readme.md', 'README', 'skill.md'):
        try:
            full = _safe_join(module_dir(module), name)
        except ScanError:
            continue
        if os.path.isfile(full):
            with open(full, 'r', encoding='utf-8', errors='replace') as f:
                return {'path': name, 'text': redact(f.read(int(chars)))}
    return {'path': None, 'text': ''}


def brief(module, focus=None, depth=3):
    """Everything an agent needs to form one specific opinion, in one call.

    Deliberately includes what has already been filed: the fastest way to
    waste an owner's attention is to recommend the thing three agents have
    recommended already.
    """
    import recs                                     # local: recs imports scan
    cfg = config(module)
    pack = {
        'module': module,
        'owner': owner_of(module),
        'config': {k: cfg.get(k) for k in
                   ('name', 'title', 'version', 'description', 'port', 'app_port',
                    'base_path', 'deps', 'fns', 'anchor') if k in cfg},
        'readme': readme(module),
        'stats': stats(module),
        'tree': tree(module, depth=depth),
        'open_recommendations': [
            {'id': r['id'], 'title': r['title'], 'status': r['status'],
             'author': r['author'], 'kind': r.get('kind')}
            for r in recs.for_module(module)],
        'how_to_file': (
            'POST /advise/api/recommend with {module, title, summary, rationale, '
            'change, anchors:[{path,line,note}], patch?, kind, severity}. It goes '
            f"to {owner_of(module) or 'the deployment owner'} as pending — nothing "
            'runs until they approve, and approving files it into build\'s idea '
            'queue for them to play as an edit job.'),
    }
    if focus:
        pack['focus'] = grep(module, str(focus))
    return pack
