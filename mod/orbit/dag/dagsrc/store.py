"""store — saved graphs and run records, on disk under ~/.mod/dag.

Two directories and no database. A graph is a document you edit and re-run, so
it is a file you can read; a run is a receipt, so it is a file you can keep.
Runs are capped by count, because a graph that fans out is cheap to start and
the box is not.

    ~/.mod/dag/graphs/<name>.json     what to run
    ~/.mod/dag/runs/<run_id>.json     what happened when it ran
"""

import json
import os
import re
import time

DIR = os.environ.get('DAG_DIR', os.path.expanduser('~/.mod/dag'))
GRAPHS = os.path.join(DIR, 'graphs')
RUNS = os.path.join(DIR, 'runs')
KEEP = int(os.environ.get('DAG_KEEP_RUNS', 500))
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')


class StoreError(Exception):
    pass


def _ensure():
    for d in (GRAPHS, RUNS):
        os.makedirs(d, exist_ok=True)


def _check(name):
    name = str(name or '').strip()
    if not NAME.match(name):
        raise StoreError(f'{name!r} is not a usable name — letters, digits, . _ - '
                         'and at most 64 characters')
    return name


def _write(path, data):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, default=str, indent=2)
    os.replace(tmp, path)


# ── graphs ───────────────────────────────────────────────────────

def save_graph(name, spec):
    _ensure()
    name = _check(name)
    spec = dict(spec)
    spec.setdefault('name', name)
    _write(os.path.join(GRAPHS, name + '.json'), spec)
    return {'name': name, 'path': os.path.join(GRAPHS, name + '.json'),
            'steps': len(spec.get('steps') or []), 'saved_at': int(time.time())}


def load_graph(name):
    name = _check(name)
    path = os.path.join(GRAPHS, name + '.json')
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        have = ', '.join(g['name'] for g in graphs()[:10]) or 'none saved yet'
        raise StoreError(f'no saved graph called {name!r} — have: {have}')
    except json.JSONDecodeError as e:
        raise StoreError(f'{path} is not valid JSON any more: {e}')


def delete_graph(name):
    name = _check(name)
    path = os.path.join(GRAPHS, name + '.json')
    if not os.path.exists(path):
        raise StoreError(f'no saved graph called {name!r}')
    os.remove(path)
    return {'deleted': name}


def graphs():
    _ensure()
    out = []
    for f in sorted(os.listdir(GRAPHS)):
        if not f.endswith('.json'):
            continue
        path = os.path.join(GRAPHS, f)
        try:
            with open(path) as fh:
                spec = json.load(fh)
        except Exception as e:
            out.append({'name': f[:-5], 'broken': str(e)})
            continue
        out.append({'name': f[:-5], 'title': spec.get('title') or spec.get('name'),
                    'description': (spec.get('description') or '')[:200],
                    'steps': len(spec.get('steps') or []),
                    'inputs': list((spec.get('inputs') or {})),
                    'updated_at': int(os.path.getmtime(path))})
    return sorted(out, key=lambda g: -(g.get('updated_at') or 0))


# ── runs ─────────────────────────────────────────────────────────

def save_run(record):
    _ensure()
    _write(os.path.join(RUNS, record['id'] + '.json'), record)
    return record


def load_run(run_id):
    path = os.path.join(RUNS, _check(run_id) + '.json')
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise StoreError(f'no run {run_id!r} — GET /runs lists them')


def runs(limit=40, graph=None, status=None):
    _ensure()
    files = [f for f in os.listdir(RUNS) if f.endswith('.json')]
    files.sort(key=lambda f: os.path.getmtime(os.path.join(RUNS, f)), reverse=True)
    out = []
    for f in files:
        if len(out) >= int(limit):
            break
        try:
            with open(os.path.join(RUNS, f)) as fh:
                r = json.load(fh)
        except Exception:
            continue
        if graph and r.get('graph') != graph:
            continue
        if status and r.get('status') != status:
            continue
        out.append({k: r.get(k) for k in
                    ('id', 'graph', 'status', 'started_at', 'finished_at',
                     'duration_ms', 'calls', 'counts', 'error')})
    return out


def prune(keep=KEEP):
    """Oldest runs out. Called after every run so nobody has to remember."""
    _ensure()
    files = [os.path.join(RUNS, f) for f in os.listdir(RUNS) if f.endswith('.json')]
    if len(files) <= keep:
        return {'kept': len(files), 'removed': 0}
    files.sort(key=os.path.getmtime, reverse=True)
    removed = 0
    for path in files[keep:]:
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return {'kept': keep, 'removed': removed}
