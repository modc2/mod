"""Judges — review the agent, not just the answer.

A judge reads an episode (the goal, every action the agent took, what the
state said back, and the state's own score if it has one) and returns
`{score: 0..100, reason}`. The reason is the point: it is what the feedback
node hands the agent before its next try.

    outcome   offline: the state's own score x 100, plus how many actions
              the state refused — the reason names them
    efficiency offline: fewer steps to the same place scores higher
    panel     an orbit/judge panel (multisig of judges, POST /judge)
    spec      one orbit/judge spec, no panel (POST /score) — e.g.
              {"kind": "agent", "goal": "did it play sensibly?"}
    fn        a Python callable episode -> {score, reason}
"""

import json
import os

import requests

JUDGE_URL = os.environ.get('ARENA_JUDGE_URL', 'http://localhost:51150')


def transcript(ep, limit=40):
    """An episode as text — what a model judge reads."""
    lines = [f"Goal: {ep.get('goal', '')}"]
    for i, s in enumerate(ep.get('steps', [])[-limit:]):
        mark = '' if s.get('ok', True) else '  [refused]'
        lines.append(f"{i + 1}. action: {s.get('action')!s}{mark}")
        if s.get('note'):
            lines.append(f"   note: {s['note']}")
    lines.append(f"Finished: {ep.get('done')} · state score: {ep.get('state_score')}")
    if ep.get('final'):
        lines.append(f"Final view:\n{ep['final'][-1500:]}")
    return '\n'.join(lines)


class Judge:
    kind = 'judge'
    name = ''
    weight = 1.0

    def review(self, ep):
        raise NotImplementedError


class OutcomeJudge(Judge):
    kind = 'outcome'

    def __init__(self, name='outcome', weight=1.0, **_):
        self.name, self.weight = name, float(weight)

    def review(self, ep):
        steps = ep.get('steps', [])
        bad = [s for s in steps if not s.get('ok', True)]
        base = ep.get('state_score')
        if base is None:
            base = 1.0 if ep.get('done') else 0.0
        score = max(0, round(100 * base) - 10 * len(bad))
        why = [f"state scored {base:.2f}" + (' (finished)' if ep.get('done') else ' (did not finish)')]
        if bad:
            why.append(f"{len(bad)} refused action(s): " + ', '.join(repr(s['action']) for s in bad[:5]))
        return {'score': score, 'reason': '; '.join(why)}


class EfficiencyJudge(Judge):
    kind = 'efficiency'

    def __init__(self, name='efficiency', weight=0.5, budget=10, **_):
        self.name, self.weight, self.budget = name, float(weight), int(budget)

    def review(self, ep):
        n = len(ep.get('steps', []))
        if not ep.get('done'):
            return {'score': 0, 'reason': f'used {n} steps and never finished'}
        score = 100 if n <= self.budget else max(0, round(100 * self.budget / n))
        return {'score': score, 'reason': f'finished in {n} steps (budget {self.budget})'}


class PanelJudge(Judge):
    kind = 'panel'

    def __init__(self, panel='', name='', weight=1.0, url='', timeout=600, **_):
        self.panel, self.name, self.weight = panel, name or f'panel:{panel}', float(weight)
        self.url, self.timeout = url or JUDGE_URL, int(timeout)

    def review(self, ep):
        r = requests.post(f'{self.url}/judge', json={'panel': self.panel, 'input': transcript(ep)},
                          timeout=self.timeout)
        v = r.json()
        if v.get('error'):
            raise RuntimeError(v['error'])
        reasons = '; '.join(f"{x.get('name')}: {x.get('reason')}" for x in v.get('votes', []) if x.get('reason'))
        return {'score': v.get('average') or 0, 'reason': reasons or v.get('reason', ''),
                'verdict': v.get('id'), 'pass': v.get('pass')}


class SpecJudge(Judge):
    kind = 'spec'

    def __init__(self, spec=None, name='', weight=1.0, url='', timeout=600, **_):
        self.spec = json.loads(spec) if isinstance(spec, str) else dict(spec or {'kind': 'agent'})
        self.name = name or self.spec.get('name') or 'spec'
        self.weight, self.url, self.timeout = float(weight), url or JUDGE_URL, int(timeout)

    def review(self, ep):
        r = requests.post(f'{self.url}/score', json={'spec': self.spec, 'input': transcript(ep)},
                          timeout=self.timeout)
        v = r.json()
        if v.get('error'):
            raise RuntimeError(v['error'])
        return {'score': v.get('score') or 0, 'reason': v.get('reason', '')}


class FnJudge(Judge):
    kind = 'fn'

    def __init__(self, fn=None, name='fn', weight=1.0, **_):
        self.fn, self.name, self.weight = fn, name, float(weight)

    def review(self, ep):
        return self.fn(ep)


KINDS = {'outcome': OutcomeJudge, 'efficiency': EfficiencyJudge, 'panel': PanelJudge,
         'spec': SpecJudge, 'fn': FnJudge}


def make(spec):
    if isinstance(spec, Judge):
        return spec
    if callable(spec):
        return FnJudge(fn=spec)
    spec = dict(spec or {})
    kind = spec.pop('kind', 'outcome')
    if kind not in KINDS:
        raise ValueError(f'no judge kind `{kind}` — kinds: {sorted(KINDS)}')
    return KINDS[kind](**spec)


def bench(judges, ep):
    """Every judge reviews; a judge that errors abstains. Weighted average."""
    votes, total, weight = [], 0.0, 0.0
    for j in judges:
        try:
            v = j.review(ep)
            s = max(0.0, min(100.0, float(v.get('score') or 0)))
            votes.append({'judge': j.name, 'kind': j.kind, 'score': s, 'reason': v.get('reason', '')})
            total, weight = total + s * j.weight, weight + j.weight
        except Exception as e:                              # noqa: BLE001 — abstains, recorded
            votes.append({'judge': j.name, 'kind': j.kind, 'score': None,
                          'reason': f'abstained: {type(e).__name__}: {e}'})
    return {'score': round(total / weight, 1) if weight else None, 'votes': votes}
