"""Offline tests: rule judges only, throwaway store, no network."""

import json
import os
import sys

import pytest

# Small Merkle tree for the hash-based keys — keygen at the default 2^8
# leaves is ~1s per judge, pointless in tests. 2^3 = 8 signatures each.
os.environ.setdefault('JUDGE_XMSS_HEIGHT', '3')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import keys  # noqa: E402
import panel  # noqa: E402

LONG = {'name': 'long', 'kind': 'rule', 'min_len': 20}
CLEAN = {'name': 'clean', 'kind': 'rule', 'forbid': ['TODO']}
BROKEN = {'name': 'broken', 'kind': 'rule', 'min_len': 'not-a-number'}


@pytest.fixture
def book(tmp_path):
    return panel.Panels(str(tmp_path / 'judge.db'))


def test_create_and_params(book):
    p = book.create('pr', 'alice', [LONG, CLEAN], threshold=75)
    assert p['creator'] == 'alice' and p['threshold'] == 75
    assert p['min_votes'] == 2  # quorum defaults to the full bench
    assert book.create('pr', 'bob', [LONG])['error']  # name taken
    assert book.create('pr2', 'bob', [])['error']     # empty bench
    assert book.create('pr2', '', [LONG])['error']    # creator required
    assert book.create('Bad Name', 'bob', [LONG])['error']


def test_average_vs_threshold(book):
    book.create('pr', 'alice', [LONG, CLEAN], threshold=75)
    good = book.judge('pr', 'a perfectly reasonable sentence with no faults')
    assert good['approved'] and good['average'] == 100

    # One judge at 50 (TODO present), one at 100 → average 75, meets 75.
    edge = book.judge('pr', 'this is long enough but contains a TODO marker')
    assert edge['average'] == 75 and edge['approved']

    book.update('pr', 'alice', threshold=80)
    fail = book.judge('pr', 'this is long enough but contains a TODO marker')
    assert fail['average'] == 75 and not fail['approved']


def test_weighted_average(book):
    heavy = dict(CLEAN, weight=3)
    book.create('w', 'alice', [LONG, heavy], threshold=60)
    # long=100 (w1), clean=50 (w3) → (100 + 150) / 4 = 62.5
    v = book.judge('w', 'this is long enough but contains a TODO marker')
    assert v['average'] == 62.5 and v['approved']


def test_quorum_fails_closed(book):
    # The broken judge errors → only 1 of 2 votes, quorum is 2 → FAIL,
    # even though the one vote that landed was 100.
    book.create('q', 'alice', [CLEAN, BROKEN], threshold=10)
    v = book.judge('q', 'a clean input that would otherwise sail through')
    assert not v['approved'] and v['average'] is None
    assert 'quorum' in v['reason']
    assert [s for s in v['scores'] if s['score'] is None][0]['name'] == 'broken'

    # Creator lowers the quorum to 1 → the surviving vote decides.
    book.update('q', 'alice', min_votes=1)
    v = book.judge('q', 'a clean input that would otherwise sail through')
    assert v['approved'] and v['average'] == 100


def test_creator_only_mutation(book):
    book.create('pr', 'alice', [LONG])
    assert book.update('pr', 'mallory', threshold=0)['error']
    assert book.remove('pr', 'mallory')['error']
    assert book.get('pr')['threshold'] == 60
    assert book.remove('pr', 'alice') == {'removed': 'pr'}
    assert book.get('pr')['error']


def test_verdict_record(book):
    book.create('pr', 'alice', [CLEAN])
    v = book.judge('pr', 'first')
    book.judge('pr', 'second has a TODO')
    vs = book.verdicts('pr')
    assert len(vs) == 2 and vs[0]['input'].startswith('second')
    one = book.verdict(v['id'])
    assert one['approved'] and one['scores'][0]['name'] == 'clean'
    # Removing the panel keeps the record.
    book.remove('pr', 'alice')
    assert len(book.verdicts('pr')) == 2


def test_judge_validation(book):
    assert book.create('x', 'a', [{'kind': 'rule'}])['error']            # no name
    assert book.create('x', 'a', [LONG, LONG])['error']                  # dup name
    assert book.create('x', 'a', [{'name': 'j', 'kind': 'llm'}])['error']  # no prompt
    assert book.create('x', 'a', [dict(LONG, weight=0)])['error']        # bad weight
    assert book.create('x', 'a', [LONG], min_votes=5)['error']           # quorum > bench
    assert book.judge('nope', 'x')['error']                              # no panel
    book.create('x', 'a', [LONG])
    assert book.judge('x', '')['error']                                  # no input


# ── keyrings: multiple key types, quantum-resistant included ──────


def test_key_kinds_include_quantum_resistant():
    kinds = keys.kinds()
    avail = keys.available()
    assert len(avail) >= 2, 'a judge ring must hold multiple key types'
    assert any(kinds[k]['quantum_resistant'] for k in avail)
    assert 'wots-sha256' in avail  # the stdlib PQ fallback is always there


def test_new_judges_get_keyrings(book):
    p = book.create('pr', 'alice', [LONG, CLEAN])
    for j in p['judges']:
        ring = j['keys']
        assert set(ring) == set(keys.available())
        assert any(k['quantum_resistant'] for k in ring.values())
        for k in ring.values():
            assert k['public'] and k['fingerprint']
            assert 'secret' not in k and 'sk' not in k
    # Updating the bench issues keys only for the newcomer.
    old = {kt: k['public'] for kt, k in p['judges'][0]['keys'].items()}
    p2 = book.update('pr', 'alice', judges=[LONG, CLEAN,
                                            {'name': 'third', 'kind': 'rule'}])
    by_name = {j['name']: j for j in p2['judges']}
    assert {kt: k['public'] for kt, k in by_name['long']['keys'].items()} == old
    assert set(by_name['third']['keys']) == set(keys.available())


def test_client_supplied_keys_are_stripped(book):
    p = book.create('pr', 'alice', [dict(LONG, keys={'ed25519': {'public': 'ff'}})])
    assert p['judges'][0]['keys']['ed25519']['public'] != 'ff'
    raw = json.loads(book.db.execute(
        'SELECT judges FROM panels WHERE name=?', ('pr',)).fetchone()['judges'])
    assert 'keys' not in raw[0]  # the stored row holds params only


def test_votes_are_signed_and_verify(book):
    book.create('pr', 'alice', [LONG, CLEAN])
    v = book.judge('pr', 'a perfectly reasonable sentence with no faults')
    for s in v['scores']:
        assert set(s['sigs']) == set(keys.available())
        assert all('sig' in e for e in s['sigs'].values())
    r = book.verify_verdict(v['id'])
    assert r['verified']
    assert all(c['ok'] for j in r['judges'] for c in j['checks'].values())
    # Signatures survive panel removal — the record stands on its own.
    book.remove('pr', 'alice')
    assert book.verify_verdict(v['id'])['verified']


def test_tampered_verdict_fails_verification(book):
    book.create('pr', 'alice', [CLEAN])
    v = book.judge('pr', 'a clean input')
    scores = book.verdict(v['id'])['scores']
    scores[0]['score'] = 1.0  # doctor the recorded vote
    book.db.execute('UPDATE verdicts SET scores=? WHERE id=?',
                    (json.dumps(scores), v['id']))
    book.db.commit()
    r = book.verify_verdict(v['id'])
    assert not r['verified']
    assert not any(c['ok'] for j in r['judges'] for c in j['checks'].values())


def test_hash_key_exhaustion_degrades_gracefully(book):
    book.create('pr', 'alice', [CLEAN])
    for i in range(2 ** 3):  # spend every one-time leaf
        book.judge('pr', f'input number {i}')
    v = book.judge('pr', 'one past the tree')
    assert v['average'] == 100  # the vote itself still counts
    sigs = v['scores'][0]['sigs']
    assert 'sig' not in sigs['wots-sha256']
    assert 'exhausted' in sigs['wots-sha256']['note']
    others = [k for k in sigs if k != 'wots-sha256']
    assert others and all('sig' in sigs[k] for k in others)
    r = book.verify_verdict(v['id'])
    checks = r['judges'][0]['checks']
    assert all(checks[k]['ok'] for k in others)
    # Still verified: ml-dsa-65 keeps the quantum-resistant attestation.
    assert r['verified'] == any(
        keys.kinds()[k]['quantum_resistant'] for k in others)
