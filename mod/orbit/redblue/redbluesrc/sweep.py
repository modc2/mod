"""sweep — the same round, fired at every model a provider serves.

A round answers "how does this defense hold up on this model". A sweep asks it
of a whole catalog: pick a provider (venice | openrouter | mock), narrow the
list with the same search and scope the console shows, and every model in it
gets the identical round — same attacks, same defenses, same controls, same
judge — so the numbers that come back are comparable across labs.

    sweep record ─ ~/.mod/redblue/sweeps/<id>.json
        provider, models[]     each {model, status, round, summary}
        status                 running | done | stopped | error | interrupted
        estimate               model calls before anything was spent

WHY THE DEFAULT DEFENSE IS `none`
    "Which model is safest" means the bare model. A sweep with `layered` is a
    different, also useful question — "which model does this pipeline save" —
    and the result index labels which one was asked.

WHY A TRIPWIRE ON THE FIRST FAILURES
    A bad or missing key fails every model the same way. Without a stop, a
    465-model sweep burns through 465 identical 401s before anyone looks.
    Three consecutive models that never reached the model end the sweep, with
    the error the provider gave.

WHY MODELS RUN A FEW AT A TIME
    Matches inside one model's round are already `parallel`-wide. Models run
    `models_parallel` (default 2) at once on top of that, so a provider sees
    parallel × models_parallel requests in flight — enough to finish, not
    enough to get rate-limited into a column of ERRORs.
"""

import concurrent.futures as cf
import threading
import time
import uuid

from . import arena, catalog, corpus, defense as defmod, models, store

_ACTIVE = {}            # sweep id -> threading.Event (set = stop requested)
_lock = threading.Lock()
TRIPWIRE = 3


class SweepError(Exception):
    pass


def sweep_id():
    return 's-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:4]


def targets(provider, models_=None, q=None, scope='all', online_only=True,
            limit=0):
    """Which model strings a sweep would run. An explicit list wins; otherwise
    it is exactly the catalog view the caller is looking at."""
    provider = catalog._provider(provider)
    if models_:
        ids = models_ if isinstance(models_, list) else \
            [s.strip() for s in str(models_).split(',') if s.strip()]
        # OpenRouter ids carry their own colon (`…/r1:free`), so "has a colon"
        # does not mean "already has a provider" — only a known prefix does.
        known = tuple(p + ':' for p in (*catalog.PROVIDERS, *models.KEYS,
                                        'claude'))
        out = [i if i.startswith(known) else f'{provider}:{i}' for i in ids]
    else:
        view = catalog.listing(provider, q=q, scope=scope)['models']
        out = [r['model'] for r in view if r.get('online') or not online_only]
    seen, uniq = set(), []
    for m in out:
        if m not in seen:
            seen.add(m)
            uniq.append(m)
    return uniq[:int(limit)] if limit else uniq


def estimate(n_models, attacks, defenses, judge='heuristic', controls=True):
    """Model calls a sweep will spend, worst case (no input-stage blocks)."""
    per_model = 0
    n_ctl = len(corpus.CONTROL_SET) if controls else 0
    for d in defenses:
        turn = defmod.cost(d)['model_calls_per_turn']
        per_model += len(attacks) * (turn + (1 if judge == 'model' else 0))
        per_model += n_ctl * turn
    return {'models': n_models, 'calls_per_model': per_model,
            'calls': per_model * n_models,
            'matches_per_model': (len(attacks) + n_ctl) * len(defenses)}


def start(provider, attacks, defenses, models_=None, q=None, scope='all',
          judge='heuristic', judge_model=None, parallel=6, models_parallel=2,
          controls=True, timeout=None, limit=0, online_only=True,
          dry_run=False, background=True, resume=None):
    """Plan a sweep, and unless dry_run, run it. Returns the sweep record."""
    if resume:
        prev = store.get('sweep', resume)
        provider = prev['provider']
        models_ = [e['model'] for e in prev['models']
                   if e.get('status') not in ('done',)]
    if not attacks:
        raise SweepError('no attacks — seed the corpus or write one')
    if not defenses:
        raise SweepError('no defenses')
    provider = catalog._provider(provider)
    todo = targets(provider, models_, q=q, scope=scope,
                   online_only=_flag(online_only), limit=limit)
    if not todo:
        raise SweepError('no models match — widen the search or scope')
    judge = 'model' if str(judge).lower() == 'model' else 'heuristic'
    est = estimate(len(todo), attacks, defenses, judge, _flag(controls))
    rec = {'id': sweep_id(), 'kind': 'sweep', 'provider': provider,
           'status': 'planned' if dry_run else 'running',
           'q': q or '', 'scope': scope, 'resumed_from': resume,
           'judge': judge, 'judge_model': judge_model,
           'attacks': [a.get('id') for a in attacks],
           'defenses': [d.get('id') for d in defenses],
           'controls': _flag(controls), 'estimate': est,
           'total': len(todo), 'done': 0, 'failed': 0,
           'started': int(time.time()),
           'models': [{'model': m, 'status': 'queued'} for m in todo]}
    if dry_run:
        return rec
    if provider not in ('mock',) and not models.has_key(provider):
        raise SweepError(f'no {provider} key on this box — save one first '
                         f'(POST /keys {{provider: "{provider}", key: …}}); '
                         'listing the catalog needs none, running it does')
    store.put('sweep', rec)
    stop = threading.Event()
    with _lock:
        _ACTIVE[rec['id']] = stop
    kwargs = dict(attacks=attacks, defenses=defenses, judge=judge,
                  judge_model=judge_model, parallel=parallel,
                  models_parallel=models_parallel, controls=_flag(controls),
                  timeout=timeout)
    if not background:
        return _run(rec, stop, **kwargs)
    threading.Thread(target=_run, args=(rec, stop), kwargs=kwargs,
                     daemon=True).start()
    return rec


def _run(rec, stop, attacks, defenses, judge, judge_model, parallel,
         models_parallel, controls, timeout):
    sid = rec['id']
    lock = threading.Lock()
    streak = [0, None]

    def one(i):
        entry = rec['models'][i]
        if stop.is_set():
            return
        with lock:
            entry.update(status='running', round=f'{sid}-{i + 1}',
                         started=int(time.time()))
            store.put('sweep', rec)
        try:
            r = arena.run_round(attacks, defenses, model=entry['model'],
                                judge_kind=judge, judge_model=judge_model,
                                parallel=parallel, controls=controls,
                                timeout=timeout, name=entry['round'],
                                sweep=sid, should_stop=stop.is_set)
            summary = catalog.summarise(r) or {}
            err = _first_error(r)
        except Exception as e:                          # noqa: BLE001
            summary, err, r = {'failed': True}, str(e), {'status': 'error'}
        with lock:
            failed = bool(summary.get('failed'))
            entry.update(status='stopped' if r.get('status') == 'stopped'
                         else 'failed' if failed else 'done',
                         finished=int(time.time()),
                         summary={k: summary.get(k) for k in (
                             'safety_score', 'refusal_rate', 'over_refusal',
                             'breach_rate', 'defense', 'errors', 'attacks')},
                         error=err if failed else None)
            rec['done'] += 1
            rec['failed'] += failed
            streak[0] = streak[0] + 1 if failed else 0
            streak[1] = err if failed else None
            if streak[0] >= TRIPWIRE and not stop.is_set():
                rec['error'] = (f'{TRIPWIRE} models in a row never reached the '
                                f'model — stopping. Last error: {err}')
                stop.set()
            store.put('sweep', rec)

    try:
        with cf.ThreadPoolExecutor(max_workers=max(1, int(models_parallel))) as pool:
            list(pool.map(one, range(len(rec['models']))))
        for e in rec['models']:
            if e['status'] == 'queued':
                e['status'] = 'skipped'
        rec['status'] = 'error' if rec.get('error') else \
            'stopped' if stop.is_set() else 'done'
    except Exception as e:                              # noqa: BLE001
        rec.update(status='error', error=str(e))
    finally:
        rec['finished'] = int(time.time())
        rec['board'] = board(rec)
        store.put('sweep', rec)
        store.prune()
        with _lock:
            _ACTIVE.pop(sid, None)
    return rec


def _first_error(round_rec):
    for m in round_rec.get('matches') or []:
        if m.get('error'):
            return m['error'][:300]
    return None


def board(rec):
    """The sweep's own ranking: every model that produced a score, best first."""
    rows = [dict(model=e['model'], **(e.get('summary') or {}))
            for e in rec.get('models', []) if e.get('status') == 'done']
    rows.sort(key=lambda r: (r.get('safety_score') or 0,
                             -(r.get('over_refusal') or 0)), reverse=True)
    for i, r in enumerate(rows):
        r['rank'] = i + 1
    return rows


def stop(sid):
    with _lock:
        ev = _ACTIVE.get(sid)
    if not ev:
        rec = store.get('sweep', sid)
        return {'id': sid, 'stopping': False, 'status': rec.get('status'),
                'note': 'not running in this process'}
    ev.set()
    return {'id': sid, 'stopping': True,
            'note': 'models in flight finish their current match, the rest '
                    'are skipped'}


def get(sid):
    return _live(store.get('sweep', sid))


def listing(limit=20, provider=None):
    out = []
    for r in store.listing('sweep', limit=int(limit), provider=provider):
        r = _live(r)
        out.append({k: r.get(k) for k in (
            'id', 'provider', 'status', 'total', 'done', 'failed', 'q', 'scope',
            'judge', 'defenses', 'started', 'finished', 'error')}
            | {'leader': (r.get('board') or [None])[0]})
    return {'sweeps': out}


def _live(rec):
    """A 'running' record nobody in this process is running was interrupted —
    a restart killed its thread. Say so instead of spinning forever; `resume`
    picks up the models that never finished."""
    if rec.get('status') == 'running':
        with _lock:
            alive = rec['id'] in _ACTIVE
        if not alive:
            rec = dict(rec, status='interrupted')
    return rec


def _flag(v):
    if isinstance(v, bool):
        return v
    return str(v).lower() not in ('0', 'false', 'no', 'none', '')
