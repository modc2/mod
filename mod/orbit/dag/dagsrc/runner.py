"""runner — the scheduler.

Steps run as soon as the steps they reference are done, not in the order they
were written, and independent branches run at the same time. That is the only
reason to express a job as a graph instead of a script: four tool calls that do
not need each other should cost one call's worth of waiting.

What a run guarantees:

* a step starts only after every step it references has finished;
* a step whose dependency failed does not run — it is `skipped`, and says
  which dependency, so a failed run reads as one cause and its consequences
  rather than nine unrelated errors;
* a failure does not stop a branch that never depended on it;
* the record is written to disk as it goes, so a long run can be watched, and
  a run that is killed halfway still leaves an account of itself.
"""

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import refs, store, targets
from .graph import Graph, SpecError
from .targets import StepError

MAX_ITEMS = int(os.environ.get('DAG_MAX_FANOUT', 200))
MAX_DEPTH = int(os.environ.get('DAG_MAX_DEPTH', 3))


def truthy(v):
    if isinstance(v, str):
        return v.strip().lower() not in ('', '0', 'false', 'no', 'null', 'none')
    return bool(v)


class Run:
    """One execution of one graph."""

    def __init__(self, graph, inputs=None, depth=0, on_event=None, run_id=None,
                 persist=True, dry_run=False):
        self.graph = graph if isinstance(graph, Graph) else Graph(graph)
        self.depth = int(depth)
        self.dry_run = bool(dry_run)
        self.persist = bool(persist)
        self.on_event = on_event
        self.id = run_id or ('r' + uuid.uuid4().hex[:12])
        self.inputs = self.graph.bind(inputs)
        self.lock = threading.Lock()
        self.calls = 0
        self.started_at = None
        self.deadline = None
        self.ctx = {
            'inputs': self.inputs,
            'steps': {},
            'env': refs.env_view(),
            'run': {'id': self.id, 'graph': self.graph.name, 'depth': self.depth},
        }
        self.records = {}

    # ── record keeping ───────────────────────────────────────────

    def _emit(self, kind, **payload):
        if self.on_event:
            try:
                self.on_event({'event': kind, 'run': self.id, **payload})
            except Exception:
                pass

    def _set(self, sid, record):
        with self.lock:
            self.records[sid] = record
            self.ctx['steps'][sid] = {
                'ok': record['status'] == 'ok',
                'status': record['status'],
                'out': record.get('out'),
                'error': record.get('error'),
            }
        if self.persist:
            try:
                store.save_run(self.record())
            except Exception:
                pass                       # a full disk must not fail the run

    def record(self, status=None):
        counts = {}
        for r in self.records.values():
            counts[r['status']] = counts.get(r['status'], 0) + 1
        done = len(self.records) == len(self.graph.steps)
        if status is None:
            status = ('failed' if counts.get('failed') else
                      'ok' if done else 'running')
        rec = {
            'id': self.id,
            'graph': self.graph.name,
            'status': status,
            'depth': self.depth,
            'dry_run': self.dry_run,
            'inputs': self.inputs,
            'started_at': self.started_at,
            'finished_at': getattr(self, 'finished_at', None),
            'duration_ms': getattr(self, 'duration_ms', None),
            'calls': self.calls,
            'counts': counts,
            'progress': f'{len(self.records)}/{len(self.graph.steps)}',
            'steps': [self.records.get(sid) or {'id': sid, 'status': 'pending'}
                      for sid in self.graph.order],
            'plan': self.graph.dict(),
        }
        if getattr(self, 'finished_at', None) is not None:
            # Always present once the run is over, even as null: a caller that
            # has to test for the key cannot tell "no output" from "failed".
            rec['outputs'] = getattr(self, 'outputs', None)
        if getattr(self, 'error', None):
            rec['error'] = self.error
        return rec

    # ── one step ─────────────────────────────────────────────────

    def _resolve(self, step, extra=None):
        """A step's own fields, with every ${...} filled in. Done at the last
        possible moment, so a step sees what its dependencies actually
        returned rather than what they were expected to."""
        ctx = self.ctx if not extra else {**self.ctx, **extra}
        ids = set(self.graph.by_id)
        fields = {'args': step.args, 'url': step.url, 'tool': step.tool,
                  'server': step.server, 'call': step.call, 'value': step.value,
                  'body': step.body, 'headers': step.headers, 'inputs': step.inputs,
                  'timeout': step.timeout}
        return {k: refs.resolve(v, ctx, ids) for k, v in fields.items()}

    def _resolve_shape(self, step):
        """Only the shaping fields, and only against the run context.

        Kept apart from _resolve on purpose: a foreach step's `args` mention
        ${item}, which exists per item and not here, so resolving everything
        at shaping time would fail on a step that had just succeeded.
        """
        fields = {'limit': step.limit, 'where': step.where,
                  'sort_by': step.sort_by, 'pick': step.pick}
        return {k: refs.resolve(v, self.ctx, set(self.graph.by_id))
                for k, v in fields.items()}

    def _invoke(self, step, extra=None):
        args = self._resolve(step, extra)
        if self.dry_run and step.use != 'expr':
            return {'dry_run': True, 'would_call': step.target(),
                    'with': args.get('args')}
        if step.use != 'expr':
            with self.lock:
                self.calls += 1
        if step.use == 'mcp':
            return targets.mcp(step, args, depth=self.depth)
        if step.use == 'mod':
            return targets.mod(step, args)
        if step.use == 'http':
            return targets.http(step, args)
        if step.use == 'graph':
            return self._subgraph(step, args)
        return targets.expr(step, args)

    def _subgraph(self, step, args):
        if self.depth + 1 >= MAX_DEPTH:
            raise StepError(f'graph {step.graph!r} at depth {self.depth + 1} — '
                            f'nesting stops at {MAX_DEPTH}', kind='depth')
        spec = store.load_graph(step.graph)
        sub = Run(spec, inputs=args.get('inputs') or args.get('args') or {},
                  depth=self.depth + 1, on_event=self.on_event, persist=False,
                  dry_run=self.dry_run)
        rec = sub.execute()
        with self.lock:
            self.calls += rec.get('calls') or 0
        if rec['status'] != 'ok':
            raise StepError(f'sub-graph {step.graph!r} {rec["status"]}',
                            kind='subgraph', detail=rec)
        return rec.get('outputs')

    def _attempt(self, step, extra=None):
        """One step, with its retries. Retries are for a flaky call, so a
        refusal that will refuse again — bad arguments, an unknown tool — is
        not retried."""
        last = None
        for attempt in range(step.retries + 1):
            try:
                return self._invoke(step, extra), attempt
            except StepError as e:
                last = e
                if e.kind in ('spec', 'fn', 'depth', 'protocol') or \
                        attempt >= step.retries:
                    break
                time.sleep(step.retry_delay * (2 ** attempt))
            except refs.RefError as e:
                raise StepError(str(e), kind='ref')
            except Exception as e:
                last = StepError(f'{type(e).__name__}: {e}', kind='error')
                if attempt >= step.retries:
                    break
                time.sleep(step.retry_delay * (2 ** attempt))
        raise last

    def _run_step(self, sid):
        step = self.graph.by_id[sid]
        t0 = time.time()
        base = {'id': sid, 'use': step.use, 'target': step.target(),
                'started_at': round(t0, 3), 'needs': step.needs}

        blocked = [d for d in step.needs
                   if self.ctx['steps'].get(d, {}).get('status') != 'ok']
        if blocked:
            return self._done(base, 'skipped', t0, reason=(
                f'{", ".join(blocked)} did not succeed'))
        try:
            gate = self._gate(step)
        except (StepError, refs.RefError) as e:
            return self._done(base, 'failed', t0, error=str(e), kind='ref')
        if gate is not None:
            return self._done(base, 'skipped', t0, reason=gate)

        try:
            if step.foreach is not None:
                out, attempts = self._fanout(step), 0
            else:
                out, attempts = self._attempt(step)
            out = targets.shape(out, step, self._resolve_shape(step))
            return self._done(base, 'ok', t0, out=out,
                              retries=attempts or None)
        except StepError as e:
            status = 'ok' if step.continue_on_error else 'failed'
            rec = self._done(base, status, t0, error=str(e), kind=e.kind,
                             detail=e.detail,
                             out=None if status == 'failed' else e.dict())
            return rec
        except refs.RefError as e:
            status = 'ok' if step.continue_on_error else 'failed'
            return self._done(base, status, t0, error=str(e), kind='ref')
        except Exception as e:
            return self._done(base, 'failed', t0,
                              error=f'{type(e).__name__}: {e}', kind='internal')

    def _gate(self, step):
        """`if` / `unless`. Returns None to run, or why it was skipped."""
        ids = set(self.graph.by_id)
        cond = getattr(step, 'if')
        if cond is not None and not truthy(refs.resolve(cond, self.ctx, ids)):
            return f'if {cond!r} was false'
        if step.unless is not None and truthy(refs.resolve(step.unless, self.ctx, ids)):
            return f'unless {step.unless!r} was true'
        return None

    def _fanout(self, step):
        """foreach: the same call once per item, in parallel, results in order."""
        ids = set(self.graph.by_id)
        items = refs.resolve(step.foreach, self.ctx, ids)
        if isinstance(items, dict):
            items = [{'key': k, 'value': v} for k, v in items.items()]
        if items is None:
            items = []
        if not isinstance(items, list):
            raise StepError(f'foreach wants a list, got {type(items).__name__} — '
                            f'{json.dumps(items, default=str)[:120]}', kind='spec')
        if len(items) > MAX_ITEMS:
            raise StepError(f'foreach over {len(items)} items — the cap is '
                            f'{MAX_ITEMS} (set DAG_MAX_FANOUT to raise it)',
                            kind='fanout')
        if not items:
            return []
        width = min(step.concurrency or self.graph.max_parallel, len(items), 32)
        out = [None] * len(items)

        def one(i):
            try:
                value, _ = self._attempt(step, {'item': items[i], 'index': i})
                return i, value, None
            except StepError as e:
                return i, None, e

        with ThreadPoolExecutor(max_workers=width) as pool:
            for i, value, err in pool.map(one, range(len(items))):
                if err and not step.continue_on_error:
                    raise StepError(f'item {i} of {len(items)}: {err}',
                                    kind=err.kind, detail=err.detail)
                out[i] = err.dict() if err else value
        return out

    def _done(self, base, status, t0, **extra):
        rec = {**base, 'status': status,
               'duration_ms': int((time.time() - t0) * 1000),
               **{k: v for k, v in extra.items() if v is not None}}
        self._set(base['id'], rec)
        self._emit('step', step=base['id'], status=status,
                   duration_ms=rec['duration_ms'],
                   error=rec.get('error'), reason=rec.get('reason'))
        return rec

    # ── the loop ─────────────────────────────────────────────────

    def execute(self):
        self.started_at = round(time.time(), 3)
        self.deadline = (self.started_at + self.graph.timeout
                         if self.graph.timeout else None)
        self.error = None
        self.outputs = None
        self._emit('start', graph=self.graph.name, steps=len(self.graph.steps),
                   levels=self.graph.levels())

        pending = list(self.graph.order)
        running = {}
        pool = ThreadPoolExecutor(max_workers=self.graph.max_parallel)
        try:
            while pending or running:
                ready = [sid for sid in pending
                         if all(d in self.records for d in self.graph.by_id[sid].needs)]
                for sid in ready[:max(0, self.graph.max_parallel - len(running))]:
                    pending.remove(sid)
                    self._emit('step_start', step=sid,
                               target=self.graph.by_id[sid].target())
                    running[pool.submit(self._run_step, sid)] = sid
                if not running:
                    # Nothing ready and nothing in flight: only reachable if a
                    # dependency was never recorded, which _topo already ruled out.
                    break
                done = next(iter(_wait_first(running, self.deadline)), None)
                if done is None:
                    self.error = (f'the graph passed its {self.graph.timeout:g}s '
                                  'timeout with steps still running')
                    for sid in list(running.values()) + pending:
                        self.records.setdefault(sid, {
                            'id': sid, 'status': 'skipped',
                            'reason': 'the run timed out'})
                    break
                running.pop(done, None)
        finally:
            pool.shutdown(wait=False)

        self.finished_at = round(time.time(), 3)
        self.duration_ms = int((self.finished_at - self.started_at) * 1000)
        self.outputs = self._outputs()
        status = ('failed' if self.error or
                  any(r.get('status') == 'failed' for r in self.records.values())
                  else 'ok')
        rec = self.record(status)
        if self.persist:
            store.save_run(rec)
            store.prune()
        self._emit('done', status=status, duration_ms=self.duration_ms,
                   calls=self.calls)
        return rec

    def _outputs(self):
        """What the run returns. `output` in the graph if it declares one,
        otherwise the leaves — the steps nothing else consumed, which is what
        the graph was for."""
        if self.graph.output is not None:
            try:
                return refs.resolve(self.graph.output, self.ctx,
                                    set(self.graph.by_id))
            except refs.RefError as e:
                self.error = f'output: {e}'
                return None
        consumed = {d for s in self.graph.steps for d in s.needs}
        leaves = [s.id for s in self.graph.steps if s.id not in consumed]
        return {sid: self.ctx['steps'].get(sid, {}).get('out') for sid in leaves}


def _wait_first(futures, deadline):
    from concurrent.futures import wait, FIRST_COMPLETED
    timeout = max(0.05, deadline - time.time()) if deadline else None
    done, _ = wait(list(futures), timeout=timeout, return_when=FIRST_COMPLETED)
    return done


def run(spec, inputs=None, **kw):
    """Parse, then execute. The one entry point everything else calls."""
    return Run(Graph(spec), inputs=inputs, **kw).execute()
