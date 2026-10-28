"""Graphs — agents, the states they act on, the judges that review them, and
the feedback that goes back around.

A graph is a document:

    {"name": "coach", "threshold": 70, "rounds": 3, "steps": 30, "seed": 1,
     "nodes": [
       {"id": "world",  "kind": "state",    "state": {"kind": "game", "game": "connect4"}},
       {"id": "player", "kind": "agent",    "agent": {"kind": "random"}, "state": "world"},
       {"id": "review", "kind": "judge",    "judges": [{"kind": "outcome"}], "of": "player"},
       {"id": "coach",  "kind": "feedback", "to": "player"},
       {"id": "end",    "kind": "end"}],
     "edges": [
       {"from": "player", "to": "review"},
       {"from": "review", "to": "end",   "when": "pass"},
       {"from": "review", "to": "coach", "when": "fail"},
       {"from": "coach",  "to": "player"}]}

Node kinds:
    state     a world (states.py). Several agents may share one, or each own one.
    agent     plays one episode on its `state`: observe, act, repeat until the
              state is done or `steps` run out. Gets the latest feedback with
              every observation.
    judge     a bench (judges.py) reviews the latest episode of `of`. Passes
              when the weighted average reaches `threshold`.
    feedback  turns the last verdict into advice for agent `to`, and starts
              the next round. Plain = the judges' reasons, joined. Give it an
              `agent` (a protocol agent) and a model writes the advice instead
              — that's a feedback agent.
    end       stop.

Edges take `when`: pass | fail | (absent = always). Nothing else — a graph is
shared as a document, so there's no expression to evaluate.
"""

import json
import os
import time
import uuid

from . import actors, judges, states

STATE_DIR = os.path.expanduser(os.environ.get('ARENA_STATE', '~/.mod/arena'))
KINDS = {
    'state': 'a world the agent acts on — game | shell | mod',
    'agent': 'plays an episode on its state',
    'judge': 'a bench of judges reviews the latest episode, 0-100',
    'feedback': 'turns the verdict into advice and starts the next round',
    'end': 'stop',
}


def _dir(*parts):
    d = os.path.join(STATE_DIR, 'graphs', *parts)
    os.makedirs(d, exist_ok=True)
    return d


# ── templates ────────────────────────────────────────────────────────────────

def _loop(state, agent, bench, threshold=70, rounds=3, steps=30, coach=None):
    coach_node = {'id': 'coach', 'kind': 'feedback', 'to': 'player'}
    if coach:
        coach_node['agent'] = coach
    return {
        'threshold': threshold, 'rounds': rounds, 'steps': steps, 'seed': 1,
        'nodes': [
            {'id': 'world', 'kind': 'state', 'state': state},
            {'id': 'player', 'kind': 'agent', 'agent': agent, 'state': 'world'},
            {'id': 'review', 'kind': 'judge', 'judges': bench, 'of': 'player'},
            coach_node,
            {'id': 'end', 'kind': 'end'},
        ],
        'edges': [
            {'from': 'player', 'to': 'review'},
            {'from': 'review', 'to': 'end', 'when': 'pass'},
            {'from': 'review', 'to': 'coach', 'when': 'fail'},
            {'from': 'coach', 'to': 'player'},
        ],
    }


TEMPLATES = {
    'game': lambda: dict(_loop({'kind': 'game', 'game': 'connect4'}, {'kind': 'random'},
                               [{'kind': 'outcome'}]), name='game',
                         about='an agent plays connect4 against a random seat; an outcome judge reviews; feedback, again'),
    'shell': lambda: dict(_loop({'kind': 'shell', 'goal': 'create hello.txt containing hi',
                                 'check': 'grep -qx hi hello.txt'},
                                {'kind': 'protocol'}, [{'kind': 'outcome'}, {'kind': 'efficiency', 'budget': 3}],
                                steps=6), name='shell',
                          about='a computer: the agent runs shell commands until the check passes'),
    'judged': lambda: dict(_loop({'kind': 'game', 'game': 'connect4'}, {'kind': 'protocol'},
                                 [{'kind': 'outcome'},
                                  {'kind': 'spec', 'spec': {'kind': 'agent', 'name': 'coach',
                                                            'goal': 'did the agent play sensibly, block threats and take wins?'}}],
                                 coach={'kind': 'protocol'}), name='judged',
                           about='an agent judge (orbit/judge) reviews the play, a feedback agent writes the advice'),
}


def templates():
    return {k: v()['about'] for k, v in TEMPLATES.items()}


def template(name='game'):
    if name not in TEMPLATES:
        raise ValueError(f'no template `{name}` — {sorted(TEMPLATES)}')
    return TEMPLATES[name]()


# ── checking ─────────────────────────────────────────────────────────────────

def check(graph):
    """Problems with a graph, as a list of strings. Empty = runnable."""
    errs, nodes = [], graph.get('nodes') or []
    ids = [n.get('id') for n in nodes]
    by = {n.get('id'): n for n in nodes}
    if len(set(ids)) != len(ids) or None in ids:
        errs.append('every node needs a unique id')
    for n in nodes:
        k = n.get('kind')
        if k not in KINDS:
            errs.append(f"node {n.get('id')}: unknown kind `{k}` — {sorted(KINDS)}")
        if k == 'agent' and (by.get(n.get('state')) or {}).get('kind') != 'state':
            errs.append(f"agent {n['id']}: `state` must name a state node")
        if k == 'judge' and not n.get('judges'):
            errs.append(f"judge {n['id']}: needs `judges`")
        if k in ('judge',) and n.get('of') and (by.get(n['of']) or {}).get('kind') != 'agent':
            errs.append(f"judge {n['id']}: `of` must name an agent node")
        if k == 'feedback' and n.get('to') and (by.get(n['to']) or {}).get('kind') != 'agent':
            errs.append(f"feedback {n['id']}: `to` must name an agent node")
    for e in graph.get('edges') or []:
        if e.get('from') not in by or e.get('to') not in by:
            errs.append(f'edge {e}: unknown node')
        if e.get('when') not in (None, 'pass', 'fail', 'always'):
            errs.append(f"edge {e}: `when` is pass | fail | always")
    if not any(n.get('kind') == 'agent' for n in nodes):
        errs.append('a graph needs at least one agent')
    return errs


# ── running ──────────────────────────────────────────────────────────────────

def episode(state, actor, steps=30, seed=1, feedback=''):
    """One go: reset, then observe→act until done or out of steps."""
    obs = state.reset(seed)
    rec = {'goal': state.goal, 'steps': [], 'feedback': feedback}
    t0 = time.time()
    for _ in range(int(steps)):
        if state.done():
            break
        try:
            action = actor.act(obs, state.tools(), feedback)
        except Exception as e:                              # noqa: BLE001 — the agent failed, not the run
            rec['steps'].append({'action': None, 'ok': False, 'note': f'agent error: {type(e).__name__}: {e}'})
            break
        got = state.act(action)
        obs = got.get('observation', '')
        rec['steps'].append({'action': action, 'ok': got.get('ok', True), 'note': got.get('note', '')})
    rec.update(done=state.done(), state_score=state.score(), final=obs,
               elapsed=round(time.time() - t0, 2))
    if hasattr(state, 'result') and rec['done']:
        try:
            rec['result'] = state.result()
        except Exception:
            pass
    return rec


def advise(verdict, ep, writer=None):
    """Feedback: what the judges said, as advice for the next try."""
    said = [f"- {v['judge']} ({v['score'] if v['score'] is not None else '-'}): {v['reason']}"
            for v in verdict.get('votes', [])]
    plain = f"Your last attempt scored {verdict.get('score')}/100.\n" + '\n'.join(said)
    if not writer:
        return plain
    try:
        return writer.act(
            'You coach an agent. Here is its last attempt and how the judges scored it.\n\n'
            f'{judges.transcript(ep)}\n\n{plain}\n\n'
            'Write 1-3 short, concrete instructions for its next attempt.') or plain
    except Exception:                                       # noqa: BLE001 — fall back to the reasons
        return plain


def run(graph, seed=None, save=True, on_event=None):
    """Run a graph. Returns the full record: every episode, every verdict,
    every piece of feedback, and how it ended."""
    if isinstance(graph, str):
        graph = load(graph)
    errs = check(graph)
    if errs:
        return {'error': '; '.join(errs)}
    nodes = {n['id']: n for n in graph['nodes']}
    edges = graph.get('edges') or []
    threshold = float(graph.get('threshold', 70))
    rounds = max(1, min(20, int(graph.get('rounds', 3))))
    steps = int(graph.get('steps', 30))
    seed = int(graph.get('seed', 1) if seed is None else seed)

    built_states = {i: states.make(n['state']) for i, n in nodes.items() if n['kind'] == 'state'}
    built_actors = {i: actors.make(n['agent']) for i, n in nodes.items() if n['kind'] == 'agent'}
    benches = {i: [judges.make(j) for j in n['judges']] for i, n in nodes.items() if n['kind'] == 'judge'}
    writers = {i: actors.make(n['agent']) for i, n in nodes.items()
               if n['kind'] == 'feedback' and n.get('agent')}

    rec = {'id': uuid.uuid4().hex[:12], 'name': graph.get('name', ''), 'graph': graph,
           'started': time.time(), 'trace': [], 'rounds': [], 'status': 'running'}
    feedback = {a: '' for a in built_actors}
    last_ep, last_agent, verdict, passed = {}, None, None, None
    rnd = 0
    current = graph.get('start') or next(n['id'] for n in graph['nodes'] if n['kind'] == 'agent')

    def emit(kind, **data):
        ev = {'node': current, 'kind': kind, 'round': rnd, **data}
        rec['trace'].append(ev)
        if on_event:
            on_event(ev)

    try:
        for _ in range(rounds * max(4, len(nodes)) + 10):     # hop cap: no graph runs forever
            node = nodes[current]
            kind = node['kind']
            if kind == 'end':
                rec['status'] = 'passed' if passed else 'ended'
                break
            if kind == 'agent':
                actor = built_actors[current]
                if hasattr(actor, 'new_round'):
                    actor.new_round(rnd)
                ep = episode(built_states[node['state']], actor, node.get('steps', steps),
                             seed + rnd, feedback[current])
                last_ep[current], last_agent = ep, current
                emit('episode', agent=current, steps=len(ep['steps']), done=ep['done'],
                     state_score=ep['state_score'])
                rec['rounds'].append({'round': rnd, 'agent': current, 'episode': ep})
            elif kind == 'judge':
                of = node.get('of') or last_agent
                verdict = judges.bench(benches[current], last_ep.get(of, {}))
                t = float(node.get('threshold', threshold))
                passed = verdict['score'] is not None and verdict['score'] >= t
                verdict.update(passed=passed, threshold=t, judge=current)
                emit('verdict', score=verdict['score'], passed=passed)
                for r in reversed(rec['rounds']):
                    if r['agent'] == of:
                        r['verdict'] = verdict
                        break
            elif kind == 'feedback':
                to = node.get('to') or last_agent
                if rnd + 1 >= rounds:
                    rec['status'] = 'out of rounds'
                    break
                feedback[to] = advise(verdict or {}, last_ep.get(to, {}), writers.get(current))
                rnd += 1
                emit('feedback', to=to, text=feedback[to])
                for r in reversed(rec['rounds']):
                    if r['agent'] == to:
                        r['feedback'] = feedback[to]
                        break
            nxt = _next(edges, current, passed)
            if nxt is None:
                rec['status'] = 'passed' if passed else 'ended'
                break
            current = nxt
        else:
            rec['status'] = 'hop cap'
    except Exception as e:                                  # noqa: BLE001 — recorded on the run
        rec['status'], rec['error'] = 'error', f'{type(e).__name__}: {e}'
    finally:
        for s in built_states.values():
            s.close()

    scores = [r['verdict']['score'] for r in rec['rounds'] if r.get('verdict')]
    rec.update(finished=time.time(), best=max((s for s in scores if s is not None), default=None),
               final=scores[-1] if scores else None, scores=scores)
    if save:
        with open(os.path.join(_dir('runs'), rec['id'] + '.json'), 'w') as f:
            json.dump(rec, f, default=str)
    return rec


def _next(edges, node, passed):
    for e in edges:
        if e.get('from') != node:
            continue
        w = e.get('when')
        if w in (None, 'always') or (w == 'pass' and passed) or (w == 'fail' and not passed):
            return e['to']
    return None


# ── storage ──────────────────────────────────────────────────────────────────

def save(graph):
    name = graph.get('name') or ''
    if not name or '/' in name or name.startswith('.'):
        raise ValueError('a saved graph needs a plain `name`')
    errs = check(graph)
    if errs:
        raise ValueError('; '.join(errs))
    with open(os.path.join(_dir(), name + '.json'), 'w') as f:
        json.dump(graph, f, indent=2)
    return {'saved': name}


def load(name):
    path = os.path.join(_dir(), name + '.json')
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return template(name)


def saved():
    return sorted(f[:-5] for f in os.listdir(_dir()) if f.endswith('.json'))


def runs(limit=20):
    d = _dir('runs')
    out = []
    for f in sorted(os.listdir(d), key=lambda f: -os.path.getmtime(os.path.join(d, f)))[:int(limit)]:
        with open(os.path.join(d, f)) as fh:
            r = json.load(fh)
        out.append({k: r.get(k) for k in ('id', 'name', 'status', 'scores', 'best', 'started')})
    return out


def get_run(id):
    path = os.path.join(_dir('runs'), os.path.basename(id) + '.json')
    if not os.path.exists(path):
        raise ValueError(f'no run `{id}`')
    with open(path) as f:
        return json.load(f)
