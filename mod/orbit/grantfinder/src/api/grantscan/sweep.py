"""Sweeps — run lanes through the agent, store what comes back.

One sweep at a time per process (it spends the host's Claude account);
`concurrency` lanes run side by side inside it.
"""
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import agent, lanes as L, store

_busy = threading.Lock()
_current = {'id': None}


def run_lane(lane: dict, sweep_id: str = '', model: str = 'sonnet', budget: float = 1.5) -> dict:
    rid = uuid.uuid4().hex[:10]
    store.run_start(rid, sweep_id, lane['id'], 'lane')
    try:
        r = agent.run(agent.lane_prompt(lane), model=model, budget=budget,
                      on_step=lambda s: store.run_step(rid, s))
        m = store.upsert(r['grants'], lane=lane['id'], category=lane['category'])
        status = 'done' if r['grants'] else 'error'
        store.run_end(rid, status, found=len(r['grants']), new=m['new'], cost=r['cost'],
                      turns=r['turns'], error=r['error'])
        return {'run': rid, 'lane': lane['id'], 'found': len(r['grants']), **m,
                'cost': r['cost'], 'error': r['error']}
    except Exception as e:
        store.run_end(rid, 'error', error=str(e))
        return {'run': rid, 'lane': lane['id'], 'found': 0, 'new': 0, 'error': str(e)}


def sweep(lanes=None, category=None, model='sonnet', budget=1.5, concurrency=2,
          background=False) -> dict:
    """Run lanes (default: all). background=True returns at once with the sweep id."""
    chosen = L.pick(lanes, category)
    if not _busy.acquire(blocking=False):
        return {'error': 'a sweep is already running', 'sweep': _current['id']}
    sid = 'sw-' + time.strftime('%m%d-%H%M%S')
    _current['id'] = sid

    def go():
        try:
            with ThreadPoolExecutor(max_workers=max(1, int(concurrency))) as ex:
                return list(ex.map(lambda l: run_lane(l, sid, model, float(budget)), chosen))
        finally:
            _current['id'] = None
            _busy.release()

    if background:
        threading.Thread(target=go, daemon=True).start()
        return {'sweep': sid, 'lanes': [l['id'] for l in chosen], 'started': True}
    res = go()
    return {'sweep': sid, 'lanes': res, 'found': sum(r['found'] for r in res),
            'new': sum(r.get('new', 0) for r in res),
            'cost': round(sum(r.get('cost') or 0 for r in res), 4)}


def verify(grant_id: str, model: str = 'sonnet', budget: float = 0.5) -> dict:
    """Re-read one grant's official page and refresh its record."""
    g = store.get(grant_id)
    rid = uuid.uuid4().hex[:10]
    store.run_start(rid, '', grant_id, 'verify')
    r = agent.run(agent.verify_prompt(g), model=model, budget=budget, timeout=300,
                  on_step=lambda s: store.run_step(rid, s))
    cat = 'crypto' if 'crypto' in g['tags'] else 'open-source' if 'open-source' in g['tags'] else ''
    m = store.upsert(r['grants'][:1], lane=(g['lanes'] or [''])[0], category=cat)
    store.run_end(rid, 'done' if r['grants'] else 'error', found=len(r['grants'][:1]),
                  new=m['new'], cost=r['cost'], turns=r['turns'], error=r['error'])
    return {'run': rid, **m, 'cost': r['cost'], 'error': r['error'],
            'grant': store.get(store.gid(store.clean(r['grants'][0]))) if r['grants'] else g}


def current() -> dict:
    sid = _current['id']
    return {'running': bool(sid), 'sweep': sid, 'runs': store.runs(sid) if sid else []}
