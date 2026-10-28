"""Recursive chains: a whole tree inside one 32-byte slot.

What must hold: many wallets write into one child chain and each write is
their own post-quantum signature, not the sequencer's; a sealed head commits
to the child's root, its batch and the head before it; the parent slot holds
exactly that head, at any depth; a proof from a leaf walks to the L1 state
root and verifies with SHA3-256 alone; every tamper in that ladder is caught;
a republished batch re-derives every root, and a sequencer who rewrites one
fails the replay.
"""

import copy

import pytest

import mcp as mcpsrv
import subchain as SC
from state import StateError


def call(tool, **kw):
    return mcpsrv.call_tool(tool, kw)


def wallet(name, amount='500'):
    try:
        call('pq_wallet', action='create', name=name)
    except StateError:
        pass
    call('pq_faucet', wallet=name, amount=amount)
    return call('pq_wallet', action='show', name=name)


@pytest.fixture(scope='module')
def seq():
    return wallet('sc-seq')


@pytest.fixture(scope='module')
def alice():
    return wallet('sc-alice', '50')


@pytest.fixture(scope='module')
def bob():
    return wallet('sc-bob', '50')


@pytest.fixture(scope='module')
def app(seq):
    r = call('pq_subchain', action='create', name='app', wallet='sc-seq')
    assert r['depth'] == 1 and r['slot'] == 'chain/app'
    return r['created']


def test_create_puts_nothing_on_chain_until_it_seals(app):
    n = mcpsrv.node()
    assert n.state.entry('chain/app') is None
    show = call('pq_subchain', action='show', chain=app)
    assert show['batches'] == 0 and show['head'] is None


def test_many_wallets_write_into_one_slot(app, alice, bob):
    for w, key in (('sc-alice', 'orders/1'), ('sc-bob', 'orders/2'),
                   ('sc-alice', 'orders/3')):
        r = call('pq_subchain', action='write', chain=app, key=key,
                 data=f'{w}:{key}', wallet=w)
        assert r['queued'] and r['l1_cost'].startswith('none')
    sc = SC.load(app)
    assert len(sc['mempool']) == 3
    # every queued write carries ITS OWN witness, under its own key type
    assert {t['body']['from'] for t in sc['mempool']} == \
        {alice['address'], bob['address']}
    for tx in sc['mempool']:
        assert SC.verify_tx(tx)


def test_seal_anchors_one_hash_on_the_l1(app):
    r = call('pq_subchain', action='seal', chain=app, wallet='sc-seq',
             days=2)
    assert r['applied'] == 3 and not r['rejected']
    assert r['anchor']['scope'] == 'l1' and r['anchor']['anchored']
    e = mcpsrv.node().state.entry('chain/app')
    assert e['value'] == r['commit'] and e['value_kind'] == 'hash'
    # 32 bytes on the parent, whatever the child holds
    assert len(e['value']) == 64
    assert SC.head_commit(r['header']) == r['commit']
    assert r['header']['root'] == r['root']


def test_a_write_cannot_be_replayed_into_another_chain(app, alice):
    tx = SC.make_tx(mcpsrv.K.get('sc-alice'), app, key='orders/9',
                    value='ab' * 32)
    assert SC.verify_tx(tx)
    moved = copy.deepcopy(tx)
    moved['body']['chain'] = app + '/eu'
    assert not SC.verify_tx(moved)          # the path is inside the signature


def test_a_stranger_cannot_overwrite_your_key(app, bob):
    r = call('pq_subchain', action='write', chain=app, key='orders/1',
             data='bob was here', wallet='sc-bob')
    assert r['queued']
    sealed = call('pq_subchain', action='seal', chain=app, wallet='sc-seq')
    assert sealed['applied'] == 0
    assert 'belongs to' in sealed['rejected'][0]['why']


def test_recursion_a_subchain_inside_a_subchain(app, alice):
    kid = call('pq_subchain', action='create', name='eu', parent=app,
               wallet='sc-seq')
    assert kid['depth'] == 2 and kid['parent'] == app
    assert 'no L1 write of its own' in kid['anchor_note']
    call('pq_subchain', action='write', chain=kid['created'], key='vat/de',
         data='19%', wallet='sc-alice')
    r = call('pq_subchain', action='seal', chain=kid['created'],
             wallet='sc-seq')
    assert r['anchor']['scope'] == 'subchain' and r['anchor']['pending']
    # the child's head only reaches the parent's tree when the PARENT seals
    parent = SC.load(app)
    assert 'chain/eu' not in parent['store']
    up = call('pq_subchain', action='seal', chain=app, wallet='sc-seq')
    assert up['folded'][0]['chain'] == kid['created']
    assert SC.load(app)['store']['chain/eu']['value'] == r['commit']


def test_a_leaf_two_levels_down_proves_to_the_l1_root(app):
    eu = app + '/eu'
    b = call('pq_subchain', action='proof', chain=eu, key='vat/de')
    assert b['complete'] and b['depth'] == 3          # eu -> app -> L1
    assert [h['level'] for h in b['hops']] == ['subchain', 'subchain', 'l1']
    assert b['root'] == mcpsrv.node().state.root()
    v = b['verify']
    assert v['ok'] and v['complete'] and v['depth'] == 3
    # each hop hands the one above it the head that hop must be holding
    assert b['hops'][1]['entry']['value'] == b['hops'][0]['head']
    assert b['hops'][2]['entry']['value'] == b['hops'][1]['head']


def test_verify_is_pure_and_catches_every_tamper(app):
    good = call('pq_subchain', action='proof', chain=app + '/eu',
                key='vat/de')
    assert SC.verify_bundle(good)['ok']

    def broken(mutate):
        b = copy.deepcopy(good)
        mutate(b)
        return SC.verify_bundle(b)

    # the value at the leaf
    bad = broken(lambda b: b['hops'][0]['entry'].update(value='ff' * 32))
    assert not bad['ok'] and not bad['hops'][0]['leaf_matches']
    # a sibling in the Merkle path
    bad = broken(lambda b: b['hops'][0]['path'].append(
        {'side': 'right', 'hash': '00' * 32}))
    assert not bad['ok']
    # the head the parent slot is supposed to be holding
    bad = broken(lambda b: b['hops'][0].update(head='11' * 32))
    assert not bad['ok']
    # a head that does not commit to the root it is shown against
    bad = broken(lambda b: b['hops'][0]['header'].update(root='22' * 32))
    assert not bad['ok'] and not bad['hops'][0]['head_commits_root']
    # the L1 root itself
    bad = broken(lambda b: b['hops'][-1].update(root='33' * 32))
    assert not bad['ok']


def test_replay_rederives_every_root_from_published_batches(app):
    r = call('pq_subchain', action='replay', chain=app)
    assert r['ok'] and r['head_matches']
    assert all(s['root_matches'] and s['prev_matches'] and s['sig_valid']
               for s in r['steps'])


def test_a_rewritten_batch_is_refused(app):
    sc = SC.load(app)
    rec = sc['batches'][0]
    blob = mcpsrv._vault_store().get(rec['blob_sha3'])
    batch = SC.parse_batch(blob)
    batch['over']['root'] = 'de' * 32
    assert not SC.check_batch(batch)['sig_valid']


def test_one_slot_against_the_same_keys_on_the_l1():
    c = call('pq_subchain', action='cost', keys=10_000)
    assert c['in_a_subchain']['state_bytes'] == 32
    assert c['on_l1']['entries'] == 10_000
    assert c['ratio'] > 1000


def test_the_tree_is_the_picture():
    t = call('pq_subchain', action='tree')
    assert t['depth'] == 2 and t['subchains'] == 2
    assert t['l1_bytes'] == 32 * t['l1_slots']
    app_node = t['children'][0]
    assert app_node['children'][0]['name'] == 'eu'


def test_a_parent_cannot_be_dropped_while_it_holds_a_chain(app):
    with pytest.raises(StateError):
        call('pq_subchain', action='drop', chain=app)


def test_validator_requirements_are_measured():
    r = call('pq_node', action='requirements')
    m = r['measured']
    assert m['memory']['rss'] > 0
    assert m['code']['dependencies'] == 0 and m['code']['lines'] > 0
    assert m['disk']['bytes_per_block'] > 0
    assert m['cpu']['sha3']['hashes_per_second'] > 1000
    assert m['cpu']['witness'][0]['ok'] and \
        m['cpu']['witness'][0]['verify_ms'] > 0
    assert r['minimum']['gpu'] == 'none, ever'
    assert r['appchains']['anchor_cost']['bytes'] == 32
