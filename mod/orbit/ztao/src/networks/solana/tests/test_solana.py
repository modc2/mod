"""Solana adapter. Offline: shortvec, base58, PDAs, the legacy message
format, session layout, hints. ZTAO_NATIVE=1: cargo suite. ZTAO_LIVE=1: a
full epoch on solana-test-validator (slow: real slots)."""
import json
import os

import pytest

from ztao_src.core import stark
from ztao_src.networks.solana import client as C

HINTS = os.path.join(C.CRATE, 'tests', 'hints.json')


def test_shortvec():
    assert C.shortvec(0) == b'\x00'
    assert C.shortvec(0x7f) == b'\x7f'
    assert C.shortvec(0x80) == b'\x80\x01'
    assert C.shortvec(0x3fff) == b'\xff\x7f'


def test_b58_pads_to_a_pubkey():
    assert C.b58decode('11111111111111111111111111111111') == bytes(32)
    pk = os.urandom(32)
    assert C.b58decode(C.b58encode(pk)) == pk


def test_pda_is_off_curve():
    prog = C.b58decode('11111111111111111111111111111111')
    pda, bump = C.find_pda([b'epoch', bytes(8)], prog)
    assert len(pda) == 32 and 0 <= bump <= 255 and not C.on_ed25519(pda)


def test_compile_tx_orders_signers_first():
    payer, other = C.Keypair.generate(), C.Keypair.generate()
    prog, ro = os.urandom(32), os.urandom(32)
    ix = C.Ix(prog, [(other.pub, True, False), (ro, False, False)], b'\x07')
    raw = C.compile_tx([ix], payer, [other], bytes(32))
    assert raw[0] == 2                                    # two signatures
    msg = raw[1 + 2 * 64:]
    assert msg[:3] == bytes([2, 1, 2])                    # header
    keys = [msg[4 + 32 * i:36 + 32 * i] for i in range(msg[3])]
    assert keys[:2] == [payer.pub, other.pub]
    with pytest.raises(C.SolError):
        C.compile_tx([ix], payer, [], bytes(32))         # missing signer


def test_session_layout_grows_with_the_ring():
    a4, a8 = C.arrays(4), C.arrays(8)
    assert a4['end'] < a8['end']
    assert a4['s'] < a4['c'] < a4['l'] < a4['r'] < a4['mids'] < a4['sponge']


def test_hints_fixture_is_what_stark_derives():
    """contract/tests/hints.json (the cargo suite's input) is current."""
    hx = json.load(open(HINTS))
    I = lambda v: int(v, 16) if isinstance(v, str) else int(v)
    for case in hx['hp_cases']:
        hp, ctr = stark.hash_to_point(I(case['scope']))
        assert ctr == case['ctr'] and hp == tuple(I(v) for v in case['hp'])


def test_epoch_layout_round_trips():
    """The 56-byte header + 208-byte tag rows lib.rs writes, decoded."""
    from ztao_src.core import yuma
    assert C.EPOCH_SIZE == 56 + 32 * 208
    h = yuma.hist([90, 90, 90, 100])
    tags = [{'tag': hex(0xabc), 'work': hex(7), 'score_sum': 370, 'scores': 4,
             'claimed': True, 'top': 100, 'settled': True, 'consensus': 90,
             'rank': 360, 'hist': h},
            {'tag': hex(0xdef), 'work': hex(0), 'score_sum': 4, 'scores': 1,
             'claimed': False, 'top': 4, 'settled': False, 'consensus': 0,
             'rank': 0, 'hist': yuma.hist([4])}]
    raw = C.encode_epoch(total=374, validators=4, rank_total=360, nsettled=1,
                         tags=tags)
    assert len(raw) == C.EPOCH_SIZE
    d = C.decode_epoch(raw)
    assert (d['total'], d['validators'], d['rank_total'], d['nsettled']) == \
        (374, 4, 360, 1)
    assert d['tags'] == tags
    # the fixed offsets lib.rs uses (T_TOP 85, T_SETTLED 86, T_CONSENSUS 87,
    # T_RANK 88, T_HIST 104; header vset 28, rank_total 32, nsettled 48)
    o = 56
    assert raw[o + 85] == 100 and raw[o + 86] == 1 and raw[o + 87] == 90
    assert int.from_bytes(raw[o + 88:o + 104], 'little') == 360
    assert raw[o + 104 + 90] == 3 and raw[o + 104 + 100] == 1
    assert raw[28] == 4 and raw[48] == 1
    assert C.decode_epoch(b'zumaEP01' + raw[8:]) is None  # old layout
    assert C.decode_epoch(None) is None


def test_settle_instruction_data():
    b = C.settle_data(5, 0x1234)
    assert b[0] == 9 and len(b) == 41
    assert int.from_bytes(b[1:9], 'little') == 5
    assert int.from_bytes(b[9:41], 'big') == 0x1234


def test_settle_rows_agree_with_core_yuma():
    """What tally() would show for a settled epoch == core/yuma.epoch."""
    from ztao_src.core import yuma
    by_tag = {'0x1': [90] * 4, '0x2': [70] * 4, '0x3': [50] * 4}
    ref = yuma.epoch(by_tag, 4, 1000)
    tags = []
    for t, sc in by_tag.items():
        c, r = yuma.settle(yuma.hist(sc), 4)
        tags.append({'tag': t, 'score_sum': sum(sc), 'scores': len(sc),
                     'top': max(sc), 'settled': True, 'consensus': c,
                     'rank': r, 'hist': yuma.hist(sc)})
    d = C.decode_epoch(C.encode_epoch(validators=4, nsettled=3,
                                      rank_total=ref['rank_total'], tags=tags))
    shares = [1000 * t['rank'] // d['rank_total'] for t in d['tags']]
    assert shares == [ref['tags'][t]['share'] for t in by_tag] == [428, 333, 238]


def test_adapter_shape(nets):
    a = nets.chain('solana')
    assert a.nets == ('solana', 'solana-devnet')
    assert set(a.cost_fields) == {'cu', 'units', 'txs', 'cu_max'}
    assert callable(getattr(type(a), 'settle', None))


@pytest.mark.native
def test_cargo_suite(native_suite):
    native_suite('solana', 13)


@pytest.mark.live
def test_epoch_on_validator(live_epoch):
    r = live_epoch('solana')
    assert r['network'] == 'solana'
