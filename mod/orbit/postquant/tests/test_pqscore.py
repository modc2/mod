"""The post-quantum score, and the owner-only private key export."""

import pytest

import keys as K
import mcp as mcpsrv
from pq import pqscore
from state import StateError


def tool(_tool, **a):
    return mcpsrv.TOOLS[_tool]["handler"](a)


def test_lattice_and_hash_are_safe():
    for n in ('ML-DSA-44', 'SLH-DSA-SHAKE-128f'):
        r = pqscore.score_algo(n)
        assert r['verdict'] == 'SAFE' and r['safe'] and r['score'] == 70


def test_curve_is_not_safe_and_exposure_matters():
    hidden = pqscore.score_algo('ed25519', pk_exposed=False)
    shown = pqscore.score_algo('ed25519', pk_exposed=True)
    assert not hidden['safe'] and hidden['verdict'] == 'NOT SAFE'
    assert shown['score'] < hidden['score'] <= 15


def test_unknown_scheme():
    assert pqscore.score_algo('nope')['verdict'] == 'UNKNOWN'


def test_private_wallet_hidden_from_non_owner():
    w = {'scheme': 'private', 'inner': 'ML-DSA-44'}
    assert pqscore.score_wallet(w, owner=True)['score'] == 80
    r = pqscore.score_wallet(w, owner=False)
    assert r['verdict'] == 'HIDDEN' and r['score'] is None
    assert 'ML-DSA' not in str(r)


def test_wallet_list_carries_score():
    tool('pq_wallet', action='create', name='sc-a')
    row = next(w for w in tool('pq_wallet', action='list')['wallets']
               if w['name'] == 'sc-a')
    assert row['pq_score']['verdict'] == 'SAFE'
    assert tool('pq_wallet', action='show', name='sc-a')['pq_score']['safe']


def test_export_restores_the_same_address():
    w = tool('pq_wallet', action='create', name='sc-exp',
             scheme='SLH-DSA-SHAKE-128f')
    ex = tool('pq_wallet', action='export', name='sc-exp')
    assert len(ex['seed']) == 64 and ex['address'] == w['address']
    K.remove('sc-exp')
    back = tool('pq_wallet', **{**ex['restore'], 'name': 'sc-exp2'})
    assert back['address'] == w['address']


def test_export_private_roundtrips_inner():
    tool('pq_private', action='create', name='sc-priv', inner='ML-DSA-44')
    ex = tool('pq_wallet', action='export', name='sc-priv')
    assert ex['inner'] == 'ML-DSA-44' and ex['restore']['inner'] == 'ML-DSA-44'
    addr = ex['address']
    K.remove('sc-priv')
    assert tool('pq_wallet', **ex['restore'])['address'] == addr


def test_export_refused_for_remote_non_owner():
    tool('pq_wallet', action='create', name='sc-rem')
    mcpsrv.set_remote(True)                     # no bearer → not the owner
    try:
        with pytest.raises(StateError) as e:
            tool('pq_wallet', action='export', name='sc-rem')
        assert e.value.code == 'owner_only'
        mcpsrv.set_remote(True, owner=True)     # proven server.secret bearer
        assert tool('pq_wallet', action='export', name='sc-rem')['seed']
        mcpsrv.set_remote(False, owner=False)   # loopback relay (/mcp, agent)
        with pytest.raises(StateError):
            tool('pq_wallet', action='export', name='sc-rem')
    finally:
        mcpsrv.set_remote(False)


def test_catalog_carries_score():
    from pq import algos
    assert algos.get('ML-DSA-44').describe()['pq_score']['verdict'] == 'SAFE'
