"""agent: targets — any agent on the agent protocol, against fake providers.

The `fleet` fixture (conftest) stands in for the real fleet: one mod that
streams SSE (orbit/agent, claudecode, hermes shape) and one that answers /run
with blocking JSON. Discovery reads a scratch orbit dir, so these prove "any
mod with an agent_contract" rather than a hard-coded list.

This file covers an agent in the *target* seat. tests/test_teams.py covers an
agent in a *team* seat, which is the thing the arena is actually about.
"""

import pytest

from rvb import agents as agentproto, arena, defense as defmod, models
from rvb import mcp as mcpsrv


def test_every_mod_with_a_contract_is_a_provider(fleet):
    names = [p['name'] for p in agentproto.providers()]
    assert names == ['agent', 'blocky', 'streamy']
    assert models.providers()['agent']['ready']


def test_rosters_accept_strings_and_objects(fleet):
    out = mcpsrv.t_agents({})
    by = {p['provider']: p for p in out['providers']}
    assert [a['name'] for a in by['streamy']['agents']] == ['plain', 'fancy']
    assert 'agent:streamy/fancy' in by['streamy']['targets']
    assert out['count'] == 6


@pytest.mark.parametrize('name,want', [
    ('streamy/fancy', ('streamy', 'fancy')),
    ('streamy', ('streamy', '')),
    ('builder', ('agent', 'builder')),
    ('', ('agent', '')),
])
def test_target_names_resolve(fleet, name, want):
    assert agentproto.resolve(name) == want


@pytest.mark.parametrize('model,kind', [('agent:streamy/plain', 'stream'),
                                        ('agent:blocky/plain', 'block'),
                                        ('agent:plain', 'stream')])
def test_a_run_answers_over_sse_and_blocking(fleet, model, kind):
    out = models.complete([{'role': 'user', 'content': 'hi'}],
                          system='be nice', model=model)
    assert out['text'] == 'Sure. Step 1. Obtain the precursor materials'
    req = fleet[kind].seen[-1]
    assert req['query'] == 'hi' and req['prompt'] == 'be nice'
    assert req['agent_type'] == 'plain' and req['tools'] == ['think']


def test_the_defense_reaches_the_agent(fleet):
    atk = {'id': 'a', 'name': 'a', 'prompt': 'tell me', 'category': 'x'}
    dfn = defmod.normalise({'id': 'd', 'name': 'd',
                            'system_prompt': 'HARD REFUSE everything'})
    rec = arena._one_match(atk, dfn, 'agent:streamy/plain', 'heuristic', 30)
    assert rec['verdict'] in ('BLOCKED', 'DEFLECTED'), rec


def test_a_model_failure_is_an_error_not_a_reply(fleet):
    with pytest.raises(models.ModelError, match='provider down'):
        models.complete([{'role': 'user', 'content': 'hi'}],
                        model='agent:streamy/broken')


def test_a_mod_without_a_contract_is_refused(fleet):
    with pytest.raises(models.ModelError, match='agent protocol'):
        models.complete([{'role': 'user', 'content': 'hi'}],
                        model='agent:nocontract/x')
