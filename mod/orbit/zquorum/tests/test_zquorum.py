"""zquorum end-to-end: the circuit-backed ledger, and the same ceremony
mirrored on the ZQuorum Starknet contract (skipped without the devnet
toolchain). Uses a throwaway ledger; the chain tests reuse the module's
devnet deployment (deploying it if needed)."""
import importlib.util
import os
import uuid

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location('zquorum_mod', os.path.join(HERE, 'mod.py'))
zq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(zq)

BIN = os.path.expanduser('~/.local/bin')
needs_scarb = pytest.mark.skipif(not os.path.exists(os.path.join(BIN, 'scarb')),
                                 reason='scarb not installed')
needs_chain = pytest.mark.skipif(
    not all(os.path.exists(os.path.join(BIN, t))
            for t in ('scarb', 'sncast', 'starknet-devnet')),
    reason='starknet toolchain not installed')


@pytest.fixture
def m(tmp_path, monkeypatch):
    monkeypatch.setattr(zq, 'DATA', str(tmp_path / 'quorums.json'))
    return zq.Mod()


def ceremony(m, name, network=None, threshold=2):
    r = m.create(name, threshold, desc='test', network=network)
    assert 'error' not in r, r
    for s in ('alice', 'bob', 'carol'):
        c = m.commit(s)['commitment']
        assert 'error' not in m.join(name, c)
    return r


@needs_scarb
def test_ledger_ceremony(m):
    ceremony(m, 'local')
    assert m.vote('local', 'alice', 'yes')['accepted']
    assert 'already voted' in m.vote('local', 'alice', 'yes')['error']
    assert 'not a member' in m.vote('local', 'mallory', 'yes')['error']
    assert 'frozen' in m.join('local', '123')['error']
    assert m.vote('local', 'bob', 'yes')['tally']['reached']


@needs_chain
def test_onchain_ceremony_matches_ledger(m):
    name = f't-{uuid.uuid4().hex[:8]}'  # topics are permanent on-chain
    r = ceremony(m, name, network='devnet')
    assert r['chain'].get('anchored') == name, r['chain']
    v = m.vote(name, 'alice', 'yes')
    assert v['accepted'] and v['tx']
    m.vote(name, 'bob', 'no')
    m.vote(name, 'carol', 'yes')
    oc = m.onchain(name)
    assert oc['onchain'] and oc['match'], oc
    assert oc['chain']['reached'] and oc['chain']['root'] == v['root']


@needs_chain
def test_anchor_replays_existing_quorum(m):
    name = f't-{uuid.uuid4().hex[:8]}'
    ceremony(m, name, threshold=1)
    m.vote(name, 'carol', 'yes')
    a = m.anchor(name, 'devnet')
    assert len(a['txs']) == 1 + 3 + 1, a
    assert a['verify']['match'], a['verify']
    assert 'already anchored' in m.anchor(name, 'devnet')['error']
