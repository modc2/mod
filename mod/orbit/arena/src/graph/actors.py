"""Agents — whatever turns an observation into an action.

An agent here is `act(observation, tools, feedback) -> action`. Three kinds:

    random    picks a legal move, else a tool name — the floor every other
              agent should beat, and what the tests run offline
    protocol  any agent in the fleet over the agent protocol (orbit/agent
              POST /run), so a judge can review a claude, a liquidai LFM, a
              harness — whatever `agent_type` names
    fn        a Python callable, for wiring your own in
"""

import os
import random

import requests

from .states import legal_moves

AGENT_URL = os.environ.get('ARENA_AGENT_URL', 'http://localhost:50117/run')


class Actor:
    kind = 'actor'

    def act(self, observation, tools=(), feedback=''):
        raise NotImplementedError

    def card(self):
        return {'kind': self.kind}


class RandomActor(Actor):
    kind = 'random'

    def __init__(self, seed=0, moves=None, **_):
        self.rng = random.Random(seed)
        self.moves = list(moves or [])

    def act(self, observation, tools=(), feedback=''):
        options = legal_moves(observation) or self.moves or [t['name'] for t in tools] or ['']
        return self.rng.choice(options)


class ScriptActor(Actor):
    """Plays a fixed list of actions, one per step — and a different list per
    round if `rounds` is given, which is how the tests show feedback landing."""

    kind = 'script'

    def __init__(self, actions=None, rounds=None, **_):
        self.rounds = rounds or [actions or []]
        self.round, self.i = 0, 0

    def new_round(self, n):
        self.round, self.i = min(n, len(self.rounds) - 1), 0

    def act(self, observation, tools=(), feedback=''):
        script = self.rounds[self.round]
        a = script[self.i] if self.i < len(script) else (script[-1] if script else '')
        self.i += 1
        return a


class ProtocolActor(Actor):
    kind = 'protocol'

    def __init__(self, agent_type='', provider='', model='', steps=1, url='', timeout=300, **_):
        self.agent_type, self.provider, self.model = agent_type, provider, model
        self.steps, self.url, self.timeout = int(steps), url or AGENT_URL, int(timeout)

    def act(self, observation, tools=(), feedback=''):
        query = '\n\n'.join(filter(None, [
            observation,
            'Tools: ' + ', '.join(f"{t['name']} ({t.get('description', '')})" for t in tools) if tools else '',
            f'A judge reviewed your last attempt:\n{feedback}' if feedback else '',
            'Answer with the single action to take next and nothing else.',
        ]))
        body = {'query': query, 'steps': self.steps, 'free': True}
        for k in ('agent_type', 'provider', 'model'):
            if getattr(self, k):
                body[k] = getattr(self, k)
        r = requests.post(self.url, json=body, timeout=self.timeout)
        r.raise_for_status()
        return answer_of(r.json()).strip().strip('`').strip()

    def card(self):
        return {'kind': self.kind, 'agent_type': self.agent_type, 'provider': self.provider,
                'model': self.model}


def answer_of(run):
    """The arena's rule for an agent-protocol run: the finish step's summary,
    else the last response."""
    steps = run.get('steps') or run.get('history') or []
    for s in reversed(steps):
        if isinstance(s, dict) and s.get('tool') == 'finish':
            summary = (s.get('params') or {}).get('summary')
            if summary:
                return str(summary)
    for key in ('response', 'answer', 'result', 'output'):
        if isinstance(run.get(key), str) and run[key]:
            return run[key]
    for s in reversed(steps):
        if isinstance(s, dict) and s.get('response'):
            return str(s['response'])
    return ''


class FnActor(Actor):
    kind = 'fn'

    def __init__(self, fn=None, **_):
        self.fn = fn

    def act(self, observation, tools=(), feedback=''):
        return self.fn(observation, tools, feedback)


KINDS = {'random': RandomActor, 'script': ScriptActor, 'protocol': ProtocolActor, 'fn': FnActor}


def make(spec):
    if isinstance(spec, Actor):
        return spec
    if callable(spec):
        return FnActor(fn=spec)
    spec = dict(spec or {})
    kind = spec.pop('kind', 'random')
    if kind not in KINDS:
        raise ValueError(f'no agent kind `{kind}` — kinds: {sorted(KINDS)}')
    return KINDS[kind](**spec)
