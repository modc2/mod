"""Ethereum adapter. Offline: Hades CREATE2 address, Foundry layout, revert
parsing, gas cap, the Yuma ABI + tally/settle shapes. ZTENSOR_NATIVE=1: forge suite. ZTENSOR_LIVE=1: a full epoch on
anvil."""
import json
import os

import pytest

from ztensor_src.networks.eth import client as C


def test_hades_lives_at_its_create2_address():
    from eth_hash.auto import keccak
    h = json.load(open(C.HADES))
    salt = bytes.fromhex(h['salt'][2:])
    init = bytes.fromhex(h['init'][2:])
    addr = keccak(b'\xff' + bytes.fromhex(h['deployer'][2:]) + salt +
                  keccak(init))[12:]
    assert addr.hex() == h['address'][2:].lower()


def test_foundry_project_paths():
    toml = open(os.path.join(C.PROJECT, 'foundry.toml')).read()
    # the forge suite reads the Cairo fixture from the NEAR crate
    fx = os.path.normpath(os.path.join(
        C.PROJECT, '../../near/contract/tests/fixture.json'))
    assert os.path.exists(fx) and '../../near/contract/tests/fixture.json' in toml
    assert os.path.exists(os.path.join(C.PROJECT, 'lib', 'forge-std', 'src'))


def test_revert_reason_extraction():
    e = Exception("('execution reverted: ztensor: tag claimed', '0x08c3')")
    assert C._reason(e) == 'ztensor: tag claimed'


def test_ring_cap_fits_the_tx_gas_cap(nets):
    a = nets.chain('eth')
    # ~0.41M gas per member must keep max_ring inside EIP-7825's 2^24
    assert a.max_ring * 410_000 < C.TX_GAS_CAP
    assert a.nets == ('eth', 'eth-sepolia') and a.cost_fields == ('gas',)


def _fn(abi, name):
    return next(f for f in abi if f.get('type') == 'function' and f['name'] == name)


@pytest.mark.skipif(not os.path.exists(C.ARTIFACT), reason='forge build first')
def test_yuma_abi():
    abi = json.load(open(C.ARTIFACT))['abi']
    s = _fn(abi, 'settle')
    assert [i['type'] for i in s['inputs']] == ['uint64', 'uint256']
    assert [o['type'] for o in s['outputs']] == ['uint8', 'uint128']
    assert [o['type'] for o in _fn(abi, 'yuma')['outputs']] == ['uint8', 'uint128', 'bool']
    assert [o['type'] for o in _fn(abi, 'yumaEpoch')['outputs']] == ['uint32', 'uint128', 'uint32']
    assert any(e.get('type') == 'event' and e['name'] == 'Settled' for e in abi)


def test_tally_shape_with_yuma(monkeypatch):
    from ztensor_src.core import yuma
    # two tags as the chain would hold them after settle: [90,90,90,100], [90]
    want = {7: yuma.settle(yuma.hist([90, 90, 90, 100]), 4),
            9: yuma.settle(yuma.hist([90]), 4)}
    views = {'tagsOf': lambda e: [7, 9],
             'tally': lambda e, t: {7: (370, 4), 9: (90, 1)}[t],
             'yuma': lambda e, t: (*want[t], True),
             'claimed': lambda e, t: t == 7,
             'yumaEpoch': lambda e: (4, sum(r for _, r in want.values()), 2),
             'epochTotal': lambda e: 460}
    c = C.EthZTensor('/tmp')
    monkeypatch.setattr(c, 'view', lambda net, fn, *a: views[fn](*a))
    t = c.tally('eth', 3)
    assert t['epoch'] == 3 and t['total'] == 460
    assert (t['validators'], t['rank_total'], t['settled']) == (4, 360, 2)
    a, b = t['tags']
    assert a == {'tag': '0x7', 'score_sum': 370, 'scores': 4, 'claimed': True,
                 'consensus': 90, 'rank': 360, 'settled': True}
    assert (b['consensus'], b['rank'], b['claimed']) == (0, 0, False)


def test_settle_shape_and_revert(nets, monkeypatch):
    a = nets.chain('eth')
    calls = []

    def invoke(net, fn, *args, role='relayer'):
        calls.append((fn, args))
        if args[0] == 1:
            raise C.EthError('already settled')
        return {'hash': '0xab', 'gas': 120000, 'result': [90, 360]}
    monkeypatch.setattr(a.c, 'invoke', invoke)
    r = a.settle('eth', '2', '0x7')
    assert r == {'ok': True, 'epoch': 2, 'tag': '0x7', 'consensus': 90,
                 'rank': 360, 'tx': '0xab', 'gas': 120000}
    assert calls[0] == ('settle', (2, 7))
    with pytest.raises(RuntimeError, match='already settled'):
        a.settle('eth', 1, 7)


@pytest.mark.native
def test_forge_suite(native_suite):
    native_suite('eth', 15)


@pytest.mark.live
def test_epoch_on_anvil(live_epoch):
    r = live_epoch('eth')
    assert r['network'] == 'eth'
