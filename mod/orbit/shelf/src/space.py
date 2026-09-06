"""
Space — where the thirty-one gigabytes went.

Every module in the fleet keeps its state in `~/.mod/<name>`, by convention and
without supervision. That worked until it was 31G across 72 directories, at
which point the interesting question stopped being "what does this module
store" and became "which two of them are the reason the disk is full". This
file answers the second one.

It walks with `os.scandir` and reads `stat` only. Nothing here opens a file:
size and age are metadata, so a scan of ~46k entries costs about a tenth of a
second and can be run on a whim, and no accounting pass can leak the contents
of what it is counting. That is a security property as much as a speed one.

Symlinks are counted as links, not as their targets — a module that symlinks a
model cache elsewhere is not charged for it twice, and a loop cannot make the
walk run forever.
"""
import os
import time
from typing import Any, Dict, List, Optional

MOD_HOME = os.path.expanduser('~/.mod')

# Directories that are somebody else's problem: caches and installed trees that
# a module did not author. Counted, but flagged, because "13G of node_modules"
# and "13G of your data" call for different reactions.
VENDOR = {'node_modules', '.next', 'target', '__pycache__', '.venv', 'venv',
          '.cache', 'site-packages', '.git'}


def _walk(root: str, max_entries: int = 400_000):
    """Yield (path, stat, vendor) for every file under root. Never opens one."""
    stack = [(root, False)]
    seen = 0
    while stack:
        path, in_vendor = stack.pop()
        try:
            entries = list(os.scandir(path))
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            continue
        for entry in entries:
            seen += 1
            if seen > max_entries:          # a runaway tree must not hang a console
                return
            try:
                if entry.is_symlink():
                    # Charged as the link itself: following it would double-count
                    # a shared cache and could loop.
                    yield entry.path, entry.stat(follow_symlinks=False), in_vendor
                elif entry.is_dir(follow_symlinks=False):
                    stack.append((entry.path, in_vendor or entry.name in VENDOR))
                else:
                    yield entry.path, entry.stat(follow_symlinks=False), in_vendor
            except (OSError, ValueError):
                continue


def _blank() -> Dict[str, Any]:
    return {'bytes': 0, 'files': 0, 'vendor_bytes': 0, 'vendor_files': 0,
            'newest': 0.0, 'oldest': 0.0}


def _add(acc: Dict[str, Any], st: os.stat_result, vendor: bool):
    acc['bytes'] += st.st_size
    acc['files'] += 1
    if vendor:
        acc['vendor_bytes'] += st.st_size
        acc['vendor_files'] += 1
    if st.st_mtime > acc['newest']:
        acc['newest'] = st.st_mtime
    if not acc['oldest'] or st.st_mtime < acc['oldest']:
        acc['oldest'] = st.st_mtime


def human(n: float) -> str:
    """Bytes as something a person can compare at a glance."""
    for unit in ('B', 'K', 'M', 'G', 'T'):
        if abs(n) < 1024 or unit == 'T':
            return f'{n:.0f}{unit}' if unit == 'B' else f'{n:.1f}{unit}'
        n /= 1024.0
    return f'{n:.1f}T'


def scan(home: str = MOD_HOME, limit: int = 0) -> Dict[str, Any]:
    """Every module's state directory, largest first.

    `own_bytes` is the number to argue about: total minus vendored caches, i.e.
    what the module actually wrote as opposed to what it installed.
    """
    home = os.path.expanduser(home)
    mods: Dict[str, Dict[str, Any]] = {}
    loose = _blank()

    try:
        entries = sorted(os.scandir(home), key=lambda e: e.name)
    except FileNotFoundError:
        return {'home': home, 'exists': False, 'modules': [], 'total': _blank()}

    for entry in entries:
        if entry.is_dir(follow_symlinks=False):
            acc = mods.setdefault(entry.name, _blank())
            for _path, st, vendor in _walk(entry.path):
                _add(acc, st, vendor)
        else:
            try:
                _add(loose, entry.stat(follow_symlinks=False), False)
            except OSError:
                continue

    now = time.time()
    rows: List[Dict[str, Any]] = []
    for name, acc in mods.items():
        own = acc['bytes'] - acc['vendor_bytes']
        rows.append({
            'module': name,
            'bytes': acc['bytes'],
            'size': human(acc['bytes']),
            'own_bytes': own,
            'own_size': human(own),
            'vendor_bytes': acc['vendor_bytes'],
            'vendor_size': human(acc['vendor_bytes']),
            'files': acc['files'],
            'vendor_files': acc['vendor_files'],
            'idle_days': round((now - acc['newest']) / 86400, 1) if acc['newest'] else None,
            'path': os.path.join(home, name),
        })
    rows.sort(key=lambda r: r['bytes'], reverse=True)
    if limit:
        rows = rows[:limit]

    total = {
        'bytes': sum(r['bytes'] for r in rows) + loose['bytes'],
        'files': sum(r['files'] for r in rows) + loose['files'],
        'vendor_bytes': sum(r['vendor_bytes'] for r in rows),
        'modules': len(mods),
    }
    total['size'] = human(total['bytes'])
    total['vendor_size'] = human(total['vendor_bytes'])
    return {'home': home, 'exists': True, 'modules': rows, 'total': total,
            'loose_files': loose['files'], 'loose_bytes': loose['bytes']}


def usage(module: str, home: str = MOD_HOME, depth: int = 1,
          limit: int = 40) -> Dict[str, Any]:
    """One module's state, broken down one level in.

    This is the second question, asked after `scan` names a culprit: 13G of
    *what*, exactly — and is it one directory or a hundred thousand files.
    """
    root = os.path.join(os.path.expanduser(home), module)
    if not os.path.isdir(root):
        return {'module': module, 'exists': False, 'path': root}

    groups: Dict[str, Dict[str, Any]] = {}
    for path, st, vendor in _walk(root):
        rel = os.path.relpath(path, root)
        parts = rel.split(os.sep)
        label = os.sep.join(parts[:depth]) if len(parts) > depth else rel
        _add(groups.setdefault(label, _blank()), st, vendor)

    rows = [{'name': name, 'bytes': acc['bytes'], 'size': human(acc['bytes']),
             'files': acc['files'], 'vendor': bool(acc['vendor_files'])}
            for name, acc in groups.items()]
    rows.sort(key=lambda r: r['bytes'], reverse=True)
    total = sum(r['bytes'] for r in rows)
    return {'module': module, 'exists': True, 'path': root,
            'bytes': total, 'size': human(total),
            'entries': rows[:limit], 'truncated': max(0, len(rows) - limit)}


def big(home: str = MOD_HOME, limit: int = 25,
        module: Optional[str] = None) -> Dict[str, Any]:
    """The largest individual files, wherever they are.

    Aggregates lie in one specific way — a directory can be big because it
    holds one enormous file or a million small ones, and the fix differs — so
    this exists to disambiguate.
    """
    root = os.path.join(os.path.expanduser(home), module) if module \
        else os.path.expanduser(home)
    found = []
    for path, st, vendor in _walk(root):
        found.append((st.st_size, path, st.st_mtime, vendor))
    found.sort(reverse=True)
    now = time.time()
    return {'root': root, 'files': [
        {'path': p.replace(os.path.expanduser('~'), '~'),
         'bytes': size, 'size': human(size), 'vendor': vendor,
         'age_days': round((now - mtime) / 86400, 1)}
        for size, p, mtime, vendor in found[:limit]]}
