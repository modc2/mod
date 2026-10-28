"""postquant adapter. Offline: the pqc1 wire (encoders, state decoder, revert
table) against contract/src/lib.rs. ZTAO_NATIVE=1: the cargo suite.
ZTAO_LIVE=1: a full epoch on our own postquant node, then every receipt
replayed."""
import hashlib
import os
import re

import pytest

from ztao_src.networks.postquant import client as C

LIB = os.path.join(C.CRATE, 'src', 'lib.rs')


def _lib():
    return open(LIB).read()


def test_revert_table_matches_the_contract():
    codes = {int(v): k for k, v in
             re.findall(r'pub const (E_\w+): i32 = (-\d+);', _lib())}
    assert set(codes) == set(C.ERRORS), set(codes) ^ set(C.ERRORS)


def test_limits_match_the_contract():
    lib = _lib()
    assert f'pub const MAX_RING: u32 = {C.MAX_RING};' in lib
    assert 'const MAGIC: &[u8; 4] = b"ZTP1";' in lib


def test_text_felt_is_nears_rule():
    v = C.text_felt('ztao-pq/contract/x')
    d = bytearray(hashlib.sha256(b'ztao-pq/contract/x').digest())
    d[0] &= 0x07
    assert v == int.from_bytes(d, 'big') and v < 2 ** 251


def test_felts_are_never_reduced():
    with pytest.raises(ValueError):
        C._f(C.PRIME)
    assert C._f(1) == bytes(31) + b'\x01'


def _sig(n):
    pt = {'x': '0x1', 'y': '0x2'}
    return {'ring': [pt] * n, 'c0': '0x3', 's': ['0x4'] * n, 'image': pt}


def test_input_layouts():
    assert C.enc_tick(5) == b'\x07' + (5).to_bytes(8, 'little')
    init = C.enc_init(1, 0xabc, 40, 3, 1000)
    assert len(init) == 1 + 8 + 32 + 8 + 4 + 16
    reg = C.enc_register(9, 2, {'x': 1, 'y': 2})
    assert reg[:10] == b'\x01' + (9).to_bytes(8, 'little') + b'\x02'
    assert len(reg) == 10 + 64
    for n in (3, 4, 64):
        s = C.enc_sig(_sig(n))
        assert len(s) == 1 + 64 * n + 32 + 32 * n + 64 + 64
        assert s.endswith(bytes(64))          # no hint = (0, 0)
    hp = {'x': 7, 'y': 8}
    assert C.enc_sig(_sig(3), hp).endswith(C._pt(hp))
    sub = C.enc_submit(1, 0x77, _sig(4))
    assert sub[0] == 3 and sub[9:41] == C._f(0x77)
    sc = C.enc_score(1, 0x55, 90, _sig(4))
    assert sc[0] == 4 and sc[41] == 90
    cl = C.enc_claim(1, 6, 0xbeef, _sig(4))
    assert cl[0] == 6 and cl[9:17] == (6).to_bytes(8, 'little')
    # the biggest ring still fits the node's input cap (16 KiB)
    assert len(C.enc_claim(1, 6, 1, _sig(C.MAX_RING))) < 16 << 10


def _state_bytes():
    """A state built by hand in lib.rs State::write order."""
    u = lambda v, n: int(v).to_bytes(n, 'little')  # noqa: E731
    f = C._f
    b = b'ZTP1' + f(0xabc) + u(40, 8) + u(3, 4) + u(1000, 16) + b'\x01' + \
        u(123, 8)
    b += u(1, 4) + f(1) + f(2)                       # miners
    b += u(0, 4)                                     # validators
    b += u(1, 4) + u(3, 8) + u(4, 4) + u(170, 16) + u(160, 16) + u(1, 4)
    b += u(1, 4) + f(0x55) + f(0x77) + u(170, 16) + u(2, 4) + b'\x01\x01' + \
        bytes([80]) + u(160, 16) + bytes([2]) + bytes([80]) + u(1, 4) + \
        bytes([90]) + u(1, 4)
    b += u(1, 4) + f(0x99)                           # nullifiers
    b += u(1, 4) + f(0xbeef) + u(1000, 16)           # credit
    return b


def test_decode_state():
    s = C.decode_state(_state_bytes())
    assert s['tempo'] == 40 and s['min_ring'] == 3 and s['open']
    assert s['height'] == 123 and s['miners'] == [{'x': '0x1', 'y': '0x2'}]
    ep = s['epochs'][0]
    assert (ep['epoch'], ep['vset'], ep['rank_total'], ep['nsettled']) == \
        (3, 4, 160, 1)
    t = ep['tags'][0]
    assert t['tag'] == '0x55' and t['claimed'] and t['settled']
    assert (t['consensus'], t['rank'], t['hist']) == (80, 160, [(80, 1), (90, 1)])
    assert ep['nullifiers'] == ['0x99'] and s['credit'] == {'0xbeef': 1000}
    assert C.decode_state(b'') is None
    with pytest.raises(C.PQError):
        C.decode_state(_state_bytes() + b'\0')
    with pytest.raises(C.PQError):
        C.decode_state(b'ZTP0' + _state_bytes()[4:])


def test_adapter_shape(nets):
    a = nets.chain('postquant')
    assert a.nets == ('postquant', 'postquant-1') and a.max_ring == 64
    assert a.cost_fields == ('gas', 'run_ms')
    assert a.ui['color'] == '#9d7bff'


class _Fake:
    """Stands in for PostquantZTao: records the run inputs."""
    def __init__(self):
        self.inputs = []

    def dep(self, net):
        return {'tempo': 40, 'subnet': '0xabc', 'contract': 'z'}

    def block(self, net):
        return 85

    def run(self, net, inp):
        self.inputs.append(inp)
        return {'tx': 't', 'gas': 1, 'run_ms': 0.1, 'receipt': 'r',
                'state': None}


def test_submit_signs_the_mine_scope_and_hints_hp(nets, monkeypatch):
    a = nets.chain('postquant')
    fake = _Fake()
    monkeypatch.setattr(a, 'c', fake)
    seen = {}

    def sign(name, scope, msg, ring):
        seen.update(scope=scope, msg=msg)
        return _sig(3) | {'tag': '0x5'}, 1

    monkeypatch.setattr(a.signer, 'timed_sign', sign)
    monkeypatch.setattr(a.signer, 'hash_point', lambda s: {'x': 9, 'y': 10})
    r = a.submit('postquant', 'm', 0x77, [])
    assert r['epoch'] == 2 and r['tag'] == '0x5'
    assert seen['scope'] == a.signer.scope('mine', 0xabc, 2)
    inp = fake.inputs[0]
    assert inp[0] == 3 and int.from_bytes(inp[1:9], 'little') == 85
    assert inp.endswith(C._pt({'x': 9, 'y': 10}))


@pytest.mark.native
def test_native_suite(native_suite):
    native_suite('postquant', 3)


@pytest.mark.live
def test_epoch_on_local_net(live_epoch):
    r = live_epoch('postquant')
    assert r['yuma_ok']
    au = r['audit']
    assert au['ok'] and au['linked'] and not au['invalid'], au
    assert au['receipts'] >= 20 and au['schemes'] == ['ML-DSA-44']
