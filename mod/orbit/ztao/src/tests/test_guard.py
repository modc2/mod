"""core/guard.py: the pre-flight checks submit/score/claim run before
signing, so a tx the chain would revert is never paid for."""
import pytest

from ztao_src.core import guard


def test_score_value():
    assert guard.score_value('42') == 42 and guard.score_value(100.0) == 100
    for bad in (-1, 101, 2.5, 'x', None):
        with pytest.raises(ValueError):
            guard.score_value(bad)


def test_ring_size():
    assert guard.ring_size([1, 2, 3]) == 3
    with pytest.raises(ValueError, match='below min_ring'):
        guard.ring_size([1, 2])
    assert guard.ring_size([1, 2], min_ring=2) == 2


TALLY = {'epoch': 7, 'tags': [{'tag': '0xa', 'settled': True, 'claimed': False},
                              {'tag': '0xb', 'settled': True, 'claimed': True}]}


def test_tag_open():
    guard.tag_open(TALLY, 10)  # same felt, different form
    with pytest.raises(ValueError, match='nothing to score'):
        guard.tag_open(TALLY, '0xc')


def test_claimable():
    guard.claimable(TALLY, '0xa')
    with pytest.raises(ValueError, match='already claimed'):
        guard.claimable(TALLY, '0xb')
    with pytest.raises(ValueError, match='did not submit'):
        guard.claimable(TALLY, '0xc')
    with pytest.raises(ValueError, match='no submissions'):
        guard.claimable({'epoch': 1, 'tags': []})
    open_ = {'epoch': 2, 'tags': [{'tag': '0xa', 'settled': False}]}
    with pytest.raises(ValueError, match='not settled'):
        guard.claimable(open_, '0xa')


def test_anonymity():
    r = guard.anonymity(64, None, 32)
    assert r['ring'] == r['crowd'] == 32 and r['rings'] == 2 and r['bits'] == 5
    assert not r['whole_set'] and any('block of 32' in w for w in r['warnings'])
    r = guard.anonymity(5, 16, 32)
    assert r['ring'] == 5 and r['whole_set'] and any('aim for 8' in w for w in r['warnings'])
    assert guard.anonymity(2, None, 32)['warnings'][0].startswith('only 2')
    assert guard.anonymity(0, None, 32)['ring'] == 0
    assert guard.anonymity(33, None, 32)['rings'] == 2


def test_mod_exposes_new_fns():
    from ztao_src import mod  # noqa: F401  (loads via the package path)
    import importlib
    m = importlib.import_module('ztao_src.mod').Mod()
    assert {'preflight', 'anonymity'} <= set(m.config['fns'])
    with pytest.raises(Exception):
        m.preflight('bogus', 'nobody', network='nope')
