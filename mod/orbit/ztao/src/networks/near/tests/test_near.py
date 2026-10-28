"""NEAR adapter. Offline: base58, borsh actions, text felts, build config.
ZTAO_NATIVE=1: cargo suite. ZTAO_LIVE=1: a full epoch on near-sandbox."""
import hashlib
import json
import os

import pytest

from ztao_src.networks.near import client as C


def test_b58_roundtrip():
    for raw in (b'\0\0abc', os.urandom(32), b'\xff' * 32):
        assert C.b58decode(C.b58encode(raw)) == raw
    assert C.b58encode(b'\0\0\x01') == '112'


def test_borsh_function_call():
    a = C.a_call('submit', {'k': 1}, gas=7, deposit=3)
    args = json.dumps({'k': 1}).encode()
    assert a == (b'\x02' + (6).to_bytes(4, 'little') + b'submit' +
                 len(args).to_bytes(4, 'little') + args +
                 (7).to_bytes(8, 'little') + (3).to_bytes(16, 'little'))


def test_borsh_account_actions():
    pub = bytes(range(32))
    assert C.a_create_account() == b'\x00'
    assert C.a_transfer(1) == b'\x03' + (1).to_bytes(16, 'little')
    # AddKey: ed25519 tag, nonce 0, FullAccess
    assert C.a_add_full_key(pub) == b'\x05\x00' + pub + bytes(8) + b'\x01'


def test_text_felt_matches_contract_rule():
    v = C.text_felt('ztao-1.test.near')
    d = bytearray(hashlib.sha256(b'ztao-1.test.near').digest())
    d[0] &= 0x07
    assert v == int.from_bytes(d, 'big') and v < 2 ** 251


def test_build_flags_keep_the_nearcore_traps_fixed():
    # see client.py: MVP target + --cfg near, deploy the LOWERED wasm
    assert '--cfg near' in C.RUSTFLAGS and 'target-cpu=mvp' in C.RUSTFLAGS
    assert C.WASM.endswith('.lowered.wasm')
    assert os.path.isdir(os.path.join(C.LOWER_SRC, 'src'))
    assert os.path.exists(os.path.join(C.CRATE, 'tests', 'fixture.json'))


def test_adapter_shape(nets):
    a = nets.chain('near')
    assert a.nets == ('near', 'near-testnet') and a.max_ring == 32
    assert a.cost_fields == ('tgas',)
    assert {r['ring'] for r in a.default_costs['rows']} >= {4, 32}


class _FakeClient:
    """Stands in for NearZTao: canned RPC answers, records the calls."""
    def __init__(self, invoke=None, views=None):
        self.calls, self._invoke, self._views = [], invoke, views or {}

    def invoke(self, net, method, args, as_owner=False):
        self.calls.append((method, args))
        if isinstance(self._invoke, Exception):
            raise self._invoke
        return self._invoke

    def view(self, net, method, args=None):
        return self._views[method]


def _near(nets, fake):
    a = type(nets.chain('near'))(nets.chain('near').signer)
    a.c = fake
    return a


def test_settle_shape_and_args(nets):
    fake = _FakeClient(invoke={'hash': 'H', 'tgas': 3.1, 'logs': [],
                               'result': {'consensus': 90, 'rank': '360',
                                          'settled': True}})
    r = _near(nets, fake).settle('near', '7', '0x00ab')
    assert fake.calls == [('settle', {'epoch': 7, 'tag': '0xab'})]
    assert r == {'ok': True, 'epoch': 7, 'tag': '0xab', 'consensus': 90,
                 'rank': 360, 'tx': 'H', 'tgas': 3.1}


def test_settle_replay_is_a_runtime_error(nets):
    fake = _FakeClient(invoke=C.NearError('already settled'))
    with pytest.raises(RuntimeError, match='already settled'):
        _near(nets, fake).settle('near', 1, '0x1')


def test_tally_carries_yuma(nets):
    a = C.NearZTao.__new__(C.NearZTao)
    a.view = lambda net, m, args=None: {
        'epoch': 4, 'total': '627', 'validators': 4, 'rank_total': '530',
        'settled': 3, 'tags': [
            {'tag': '0xa', 'work': '0x1', 'score_sum': '370', 'scores': 4,
             'claimed': True, 'consensus': 90, 'rank': '360', 'settled': True},
            {'tag': '0xc', 'work': '0x3', 'score_sum': '77', 'scores': 1,
             'claimed': False, 'consensus': 0, 'rank': '0', 'settled': True}]}
    t = a.tally('near', 4)
    assert (t['validators'], t['rank_total'], t['settled']) == (4, 530, 3)
    assert t['tags'][0] == {'tag': '0xa', 'score_sum': 370, 'scores': 4,
                            'claimed': True, 'consensus': 90, 'rank': 360,
                            'settled': True}
    assert t['tags'][1]['rank'] == 0


def test_yuma_reference_numbers_used_by_the_cargo_suite():
    # tests.rs hard-codes these; keep them tied to core/yuma.py
    from ztao_src.core import yuma
    r = yuma.epoch({'a': [90, 90, 90, 100], 'b': [60, 40, 70, 10],
                    'c': [77]}, 4, 1000)
    assert [(t['consensus'], t['rank'], t['share'])
            for t in r['tags'].values()] == [(90, 360, 679), (60, 170, 320),
                                             (0, 0, 0)]
    r = yuma.epoch({'a': [90] * 4, 'b': [70] * 4, 'c': [50] * 4}, 4, 1000)
    assert [t['share'] for t in r['tags'].values()] == [428, 333, 238]
    assert yuma.settle(yuma.hist([60, 40, 70, 10]), 6) == (40, 130)


@pytest.mark.native
def test_cargo_suite(native_suite):
    native_suite('near', 32)


@pytest.mark.live
def test_epoch_on_sandbox(live_epoch):
    r = live_epoch('near')
    assert r['network'] == 'near'
