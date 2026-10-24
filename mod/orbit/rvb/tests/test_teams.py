"""The teams are agents.

That is the rule this module is built around, so it gets its own file: a side
is an `agent:<provider>/<agent_type>` on the agent protocol, verified against
a live roster before anything is spent, and the board ranks those agents — not
the attack prompts they fire or the defense pipelines they compose.

Everything runs against the fake fleet in conftest and a `mock:` target, so a
full agent-vs-agent game plays here offline in well under a second.
"""

import json

import pytest

from rvb import agents as agentproto, game as gamemod, mcp as mcpsrv
from rvb import standings, store


# ── a side is an agent, and nothing else ─────────────────────────

def test_a_real_agent_resolves_to_a_verified_side(fleet):
    side = agentproto.verify('streamy/fancy')
    assert side['provider'] == 'streamy' and side['agent_type'] == 'fancy'
    assert side['reachable'] and side['spec'] == 'streamy/fancy'


def test_a_provider_with_no_agent_type_takes_its_default(fleet):
    assert agentproto.verify('streamy')['agent_type'] == 'plain'


def test_a_mod_that_does_not_speak_the_protocol_cannot_hold_a_side(fleet):
    with pytest.raises(gamemod.GameError, match='does not speak the agent protocol'):
        gamemod.lineup('agent:nocontract/x', 'red')


def test_an_agent_type_off_the_roster_is_refused(fleet):
    with pytest.raises(gamemod.GameError, match='no agent'):
        gamemod.lineup('streamy/ghost', 'blue')


def test_a_prompt_cannot_hold_a_side(fleet):
    """The old shape of this module — name a defense, name an attack — is
    exactly what must not be accepted as a team any more."""
    for bad in ('', 'layered', 'prompt-only'):
        with pytest.raises(gamemod.GameError):
            gamemod.lineup(bad, 'blue')


def test_the_agent_prefix_is_optional_on_a_side(fleet):
    assert gamemod.lineup('agent:streamy/fancy', 'red')['spec'] == \
        gamemod.lineup('streamy/fancy', 'red')['spec']


def test_a_bad_lineup_is_rejected_before_the_game_is_written(fleet, clean):
    with pytest.raises(gamemod.GameError):
        gamemod.spawn('streamy/ghost', 'streamy/plain', model='mock:naive')
    assert store.listing('game', limit=0) == []


# ── a whole game, played by agents ───────────────────────────────

def _play(fleet, seeded, **kw):
    """One short game between two fake agents. Blue answers with a pipeline,
    red answers with picks — both as the briefs ask, in bare JSON."""
    ids = [a['id'] for a in seeded][:2]

    def reply(req):
        brief = req.get('prompt') or ''
        if brief.startswith('You are the BLUE'):
            return json.dumps({'name': 'wall', 'system_prompt': 'Refuse harm.',
                               'input_rules': [], 'output_rules': []})
        return json.dumps({'attacks': ids})

    fleet['stream'].reply = staticmethod(reply)
    fleet['block'].reply = staticmethod(reply)
    try:
        return gamemod.run_game('blocky/plain', 'streamy/plain',
                                model='mock:naive', judge_kind='heuristic',
                                turns=2, per_turn=2, controls=False, **kw)
    finally:
        fleet['stream'].reply = None
        fleet['block'].reply = None


def test_two_agents_play_a_game_and_both_are_named_in_the_record(fleet, clean, seeded):
    rec = _play(fleet, seeded)
    assert rec['status'] == 'done'
    assert rec['blue'] == 'streamy/plain' and rec['red'] == 'blocky/plain'
    assert rec['lineup']['blue']['url'] and rec['lineup']['red']['provider'] == 'blocky'
    assert len(rec['turns']) == 2
    # Each side actually moved — no fallback, no error.
    for t in rec['turns']:
        assert not t['blue'].get('fallback') and not t['blue'].get('error'), t
        assert not t['red'].get('fallback') and not t['red'].get('error'), t
        assert t['red']['picks']


def test_the_board_ranks_agents_not_prompts(fleet, clean, seeded):
    _play(fleet, seeded)
    board = standings.agent_board()
    assert board['games_counted'] == 1
    assert [r['agent'] for r in board['blue']] == ['streamy/plain']
    assert [r['agent'] for r in board['red']] == ['blocky/plain']
    blue = board['blue'][0]
    assert blue['rank'] == 1 and blue['games'] == 1 and blue['turns'] == 2
    assert blue['forfeit_rate'] == 0.0 and blue['opponents'] == ['blocky/plain']
    assert 'safety_score' in blue and 'breach_rate' in board['red'][0]
    h2h = board['head_to_head'][0]
    assert h2h['blue'] == 'streamy/plain' and h2h['red'] == 'blocky/plain'
    assert h2h['blue_wins'] + h2h['red_wins'] + h2h['draws'] == 1


def test_an_agent_that_cannot_move_forfeits_on_the_board(fleet, clean, seeded):
    """An agent that answers with prose instead of a move does not crash the
    game — it plays the side's fallback, and the board says so. This is the
    axis no prompt-level scoreboard can report."""
    fleet['stream'].reply = staticmethod(lambda req: 'I would rather not.')
    fleet['block'].reply = staticmethod(lambda req: 'I would rather not.')
    try:
        rec = gamemod.run_game('blocky/plain', 'streamy/plain',
                               model='mock:naive', judge_kind='heuristic',
                               turns=1, per_turn=2, controls=False)
    finally:
        fleet['stream'].reply = None
        fleet['block'].reply = None
    assert rec['status'] == 'done'
    assert rec['turns'][0]['blue']['fallback'] == 'prompt-only'
    board = standings.agent_board()
    assert board['blue'][0]['forfeit_rate'] == 1.0
    assert board['red'][0]['forfeit_rate'] == 1.0


def test_a_running_game_is_counted_but_not_scored_as_a_loss(clean):
    store.put('game', {'id': 'g-live', 'kind': 'game', 'status': 'running',
                       'blue': 'a/x', 'red': 'b/y', 'turns': [],
                       'totals': {'blue': 0.0, 'red': 0.0}})
    board = standings.agent_board()
    blue = board['blue'][0]
    assert blue['running'] == 1 and blue['losses'] == 0 and blue['draws'] == 0


# ── the surfaces agree ───────────────────────────────────────────

def test_the_default_board_is_the_agent_board(fleet, clean, seeded):
    _play(fleet, seeded)
    assert mcpsrv.t_board({})['view'] == 'agents'
    assert mcpsrv.t_board({'view': 'corpus'})['view'] == 'corpus'
    assert mcpsrv.t_board({})['blue'][0]['agent'] == 'streamy/plain'


def test_free_agents_are_the_ones_that_have_never_played(fleet, clean, seeded):
    _play(fleet, seeded)
    board = mcpsrv.t_board({'roster': True})
    free = {a['agent'] for a in board['free_agents']}
    assert 'streamy/plain' not in free and 'blocky/plain' not in free
    assert 'streamy/fancy' in free
